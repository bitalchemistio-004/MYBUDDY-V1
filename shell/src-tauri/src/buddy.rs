//! The floating buddy: a transparent, always-on-top window that is exactly
//! as large as what is drawn in it (the character, plus a bubble while one
//! is showing, plus a small pad for the effects around it).
//!
//! The page measures its content and asks for that size. The window is
//! anchored at the character's own corner (bottom-right for a buddy on the
//! right, bottom-left on the left), so when a bubble appears the window grows
//! away from the character and the character does not move. It starts where
//! the "Where it starts" setting says and stays where the user drags it.
//!
//! It never steals focus. Clicks pass through everywhere except what is
//! drawn: the page reports that "hot" rectangle, and a small thread turns
//! click-through off only while the cursor is over it.

use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::{App, AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::voice::browser_args;

pub const LABEL: &str = "buddy";
/// The window's size before the page has measured itself (logical px).
const FIRST_SIZE: (f64, f64) = (188.0, 148.0);
/// How far below the screen's middle an "edge" buddy's feet are (logical px
/// added to 58% of the work area's height).
const EDGE_DROP: f64 = 75.0;

type Rect = (i32, i32, u32, u32);

/// A window position kept fully inside a work area (both physical px).
pub fn clamp_into(pos: (i32, i32), win: (u32, u32), work: Rect) -> (i32, i32) {
    let max_x = work.0 + work.2 as i32 - win.0 as i32;
    let max_y = work.1 + work.3 as i32 - win.1 as i32;
    (pos.0.min(max_x).max(work.0), pos.1.min(max_y).max(work.1))
}

/// The window's anchor: the corner the character stands in. Bottom-right for
/// a buddy on the right of the screen, bottom-left for one on the left.
pub fn anchor_of(pos: (i32, i32), win: (u32, u32), side: &str) -> (i32, i32) {
    // "top": the notch hangs from the top edge, centred; its anchor is the middle of its top side.
    if side == "top" {
        return (pos.0 + win.0 as i32 / 2, pos.1);
    }
    (if side == "left" { pos.0 } else { pos.0 + win.0 as i32 }, pos.1 + win.1 as i32)
}

/// The window's top-left corner for an anchor and a size.
pub fn from_anchor(anchor: (i32, i32), win: (u32, u32), side: &str) -> (i32, i32) {
    if side == "top" {
        return (anchor.0 - win.0 as i32 / 2, anchor.1);   // it grows down and out to both sides; the top middle stays
    }
    (if side == "left" { anchor.0 } else { anchor.0 - win.0 as i32 }, anchor.1 - win.1 as i32)
}

/// Where the character stands when it has not been dragged: the bottom
/// corner of the work area (above the taskbar), or partway down the screen's
/// edge. Flush with the screen: the window's own small pad is the only gap.
pub fn default_anchor(style: &str, side: &str, work: Rect, scale: f64) -> (i32, i32) {
    if style == "notch" {
        return (work.0 + work.2 as i32 / 2, work.1);      // the middle of the top edge
    }
    let x = if side == "left" { work.0 } else { work.0 + work.2 as i32 };
    let bottom = work.1 + work.3 as i32;
    let y = match style {
        "edge" => (work.1 as f64 + work.3 as f64 * 0.58 + EDGE_DROP * scale).round() as i32,
        _ => bottom,
    };
    (x, y.min(bottom))
}

/// The work area an anchor belongs to: the one it is in or on the edge of.
/// None when it is on no connected screen (a monitor that was unplugged).
pub fn work_of(anchor: (i32, i32), works: &[Rect]) -> Option<Rect> {
    works.iter().copied().find(|w| {
        anchor.0 >= w.0 && anchor.0 <= w.0 + w.2 as i32 && anchor.1 >= w.1 && anchor.1 <= w.1 + w.3 as i32
    })
}

/// Top-left for a window of `win` anchored at `anchor`, kept fully on the
/// anchor's screen. None when the anchor is on no connected screen.
pub fn fit_at(anchor: (i32, i32), win: (u32, u32), side: &str, works: &[Rect]) -> Option<(i32, i32)> {
    work_of(anchor, works).map(|work| clamp_into(from_anchor(anchor, win, side), win, work))
}

pub const PANEL: &str = "panel";
/// The quick panel's size in logical pixels (the panel plus an 8 px pad for
/// its shadow).
pub const PANEL_SIZE: (f64, f64) = (380.0, 540.0);

/// Top-left (physical px) for the quick panel: beside the buddy window on
/// the screen's inner side, bottoms aligned, always inside the work area. If
/// there is no room on that side (the buddy was dragged across the screen),
/// it opens on the other side instead of on top of the character.
pub fn panel_place(side: &str, buddy: Rect, panel: (u32, u32), work: Rect, scale: f64) -> (i32, i32) {
    let (bx, by, bw, bh) = (buddy.0 as f64, buddy.1 as f64, buddy.2 as f64, buddy.3 as f64);
    let (pw, ph) = (panel.0 as f64, panel.1 as f64);
    let (wx, wy, ww, wh) = (work.0 as f64, work.1 as f64, work.2 as f64, work.3 as f64);
    let gap = 8.0 * scale;
    let (before, after) = (bx - pw, bx + bw);
    let fits_before = before >= wx + gap;
    let fits_after = after + pw <= wx + ww - gap;
    let x = match (side == "left", fits_before, fits_after) {
        (false, true, _) | (true, true, false) => before,
        (true, _, true) | (false, false, true) => after,
        _ => before,
    };
    let x = x.max(wx + gap).min(wx + ww - pw - gap);
    let y = (by + bh - ph).max(wy + gap).min(wy + wh - ph - gap);
    (x.round() as i32, y.round() as i32)
}

/// Click on the buddy: open the quick panel next to it, or close it if open.
/// It closes itself when it loses focus, so it holds no resources while unused.
#[tauri::command]
// async: creating a window inside a synchronous command deadlocks on Windows.
pub async fn open_panel(app: AppHandle, side: String) -> Result<(), String> {
    if let Some(p) = app.get_webview_window(PANEL) {
        let _ = p.close();
        return Ok(());
    }
    let buddy = app.get_webview_window(LABEL).ok_or("no buddy window")?;
    let (bp, bs) = (buddy.outer_position().map_err(|e| e.to_string())?, buddy.outer_size().map_err(|e| e.to_string())?);
    let scale = buddy.scale_factor().map_err(|e| e.to_string())?;
    let works = work_areas(&buddy)?;
    let anchor = anchor_of((bp.x, bp.y), (bs.width, bs.height), &side);
    let work = work_of(anchor, &works).or_else(|| works.first().copied()).ok_or("no monitor")?;
    let size = ((PANEL_SIZE.0 * scale).round() as u32, (PANEL_SIZE.1 * scale).round() as u32);
    let (x, y) = panel_place(&side, (bp.x, bp.y, bs.width, bs.height), size, work, scale);
    let panel = WebviewWindowBuilder::new(&app, PANEL, WebviewUrl::App("panel.html".into()))
        .title("MBUDDY")
        .inner_size(PANEL_SIZE.0, PANEL_SIZE.1)
        .position(x as f64 / scale, y as f64 / scale)
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(true)
        .additional_browser_args(&browser_args())
        .build()
        .map_err(|e| e.to_string())?;
    let closer = panel.clone();
    panel.on_window_event(move |event| {
        if let tauri::WindowEvent::Focused(false) = event {
            let _ = closer.close();
        }
    });
    Ok(())
}

#[derive(Default)]
pub struct Hot(pub Mutex<Option<(f64, f64, f64, f64)>>);

pub fn open(app: &App) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("buddy.html".into()))
        .title("MBUDDY buddy")
        .inner_size(FIRST_SIZE.0, FIRST_SIZE.1)
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .additional_browser_args(&browser_args())
        .build()?;
    app.manage(Hot::default());
    let handle = app.handle().clone();
    thread::spawn(move || click_through_loop(handle));
    Ok(())
}

/// Click-through except over what is drawn.
fn click_through_loop(app: AppHandle) {
    let mut ignoring: Option<bool> = None;
    loop {
        thread::sleep(Duration::from_millis(60));
        let Some(win) = app.get_webview_window(LABEL) else { continue };
        let hot = *app.state::<Hot>().0.lock().unwrap();
        let over = match (hot, app.cursor_position(), win.outer_position(), win.scale_factor()) {
            (Some((x, y, w, h)), Ok(c), Ok(p), Ok(s)) => {
                let (lx, ly) = ((c.x - p.x as f64) / s, (c.y - p.y as f64) / s);
                lx >= x && lx <= x + w && ly >= y && ly <= y + h
            }
            _ => false,
        };
        let ignore = !over;
        if ignoring != Some(ignore) {
            let _ = win.set_ignore_cursor_events(ignore);
            ignoring = Some(ignore);
        }
    }
}

/// The page tells us where the character (and its bubbles) are, in CSS pixels.
#[tauri::command]
pub fn buddy_hot(app: AppHandle, x: f64, y: f64, w: f64, h: f64) {
    *app.state::<Hot>().0.lock().unwrap() = if w > 0.0 && h > 0.0 { Some((x, y, w, h)) } else { None };
}

fn work_areas(win: &WebviewWindow) -> Result<Vec<Rect>, String> {
    Ok(win.available_monitors().map_err(|e| e.to_string())?.iter()
        .map(|m| { let a = m.work_area(); (a.position.x, a.position.y, a.size.width, a.size.height) })
        .collect())
}

/// Move and size in one step, so the character does not jump for a frame
/// between a move and a resize.
#[cfg(windows)]
fn move_and_size(win: &WebviewWindow, pos: (i32, i32), size: (u32, u32)) -> Result<(), String> {
    #[link(name = "user32")]
    extern "system" {
        fn SetWindowPos(hwnd: isize, after: isize, x: i32, y: i32, cx: i32, cy: i32, flags: u32) -> i32;
    }
    const SWP_NOZORDER: u32 = 0x0004;
    const SWP_NOACTIVATE: u32 = 0x0010;
    let hwnd = win.hwnd().map_err(|e| e.to_string())?.0 as isize;
    // SAFETY: the handle is this process's own live window; the call only moves and sizes it.
    let ok = unsafe { SetWindowPos(hwnd, 0, pos.0, pos.1, size.0 as i32, size.1 as i32, SWP_NOZORDER | SWP_NOACTIVATE) };
    if ok == 0 { Err("the buddy window could not be moved".into()) } else { Ok(()) }
}

#[cfg(not(windows))]
fn move_and_size(win: &WebviewWindow, pos: (i32, i32), size: (u32, u32)) -> Result<(), String> {
    win.set_size(tauri::PhysicalSize::new(size.0, size.1)).map_err(|e| e.to_string())?;
    win.set_position(tauri::PhysicalPosition::new(pos.0, pos.1)).map_err(|e| e.to_string())
}

fn physical(w: f64, h: f64, scale: f64) -> (u32, u32) {
    ((w.clamp(40.0, 1200.0) * scale).round() as u32, (h.clamp(40.0, 1200.0) * scale).round() as u32)
}

/// Put the buddy where it belongs and show it. `w`, `h`: the size of what the
/// page draws (logical px). `ax`, `ay`: the anchor the user dragged it to
/// (physical px), used when it is still on a connected screen; otherwise the
/// style's own place. Returns the anchor it ended up at.
#[tauri::command]
pub fn buddy_place(app: AppHandle, style: String, side: String, w: f64, h: f64, ax: Option<i32>, ay: Option<i32>) -> Result<(i32, i32), String> {
    let win = app.get_webview_window(LABEL).ok_or("no buddy window")?;
    let monitor = win.primary_monitor().map_err(|e| e.to_string())?.ok_or("no monitor")?;
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let primary = (area.position.x, area.position.y, area.size.width, area.size.height);
    let works = work_areas(&win)?;
    let size = physical(w, h, scale);
    let saved = match (ax, ay) {
        (Some(x), Some(y)) => fit_at((x, y), size, &side, &works),
        _ => None,
    };
    let pos = saved.unwrap_or_else(|| {
        clamp_into(from_anchor(default_anchor(&style, &side, primary, scale), size, &side), size, primary)
    });
    move_and_size(&win, pos, size)?;
    win.show().map_err(|e| e.to_string())?;
    Ok(anchor_of(pos, size, &side))
}

/// The content changed size (a bubble came or went) or the window was
/// dragged: resize around the character's corner and keep the window fully
/// on its screen. Returns the anchor, which the page remembers after a drag.
#[tauri::command]
pub fn buddy_fit(app: AppHandle, side: String, w: f64, h: f64) -> Result<(i32, i32), String> {
    let win = app.get_webview_window(LABEL).ok_or("no buddy window")?;
    let (p, s) = (win.outer_position().map_err(|e| e.to_string())?, win.outer_size().map_err(|e| e.to_string())?);
    let scale = win.scale_factor().map_err(|e| e.to_string())?;
    let size = physical(w, h, scale);
    let anchor = anchor_of((p.x, p.y), (s.width, s.height), &side);
    let works = work_areas(&win)?;
    // Dragged partly off a screen: the anchor may be outside every work area.
    // Then the window is pulled onto the screen its middle is on.
    let middle = (p.x + s.width as i32 / 2, p.y + s.height as i32 / 2);
    let pos = fit_at(anchor, size, &side, &works)
        .or_else(|| work_of(middle, &works).or_else(|| works.first().copied())
            .map(|work| clamp_into(from_anchor(anchor, size, &side), size, work)))
        .ok_or("no monitor")?;
    move_and_size(&win, pos, size)?;
    Ok(anchor_of(pos, size, &side))
}

#[cfg(test)]
mod tests {
    use super::{anchor_of, clamp_into, default_anchor, fit_at, from_anchor, panel_place, work_of};

    #[test]
    fn the_notch_hangs_from_the_middle_of_the_top_edge_and_grows_down_and_out() {
        let work = (0, 0, 1920, 1032);
        assert_eq!(default_anchor("notch", "top", work, 1.0), (960, 0));
        let small = from_anchor((960, 0), (200, 80), "top");
        let big = from_anchor((960, 0), (650, 410), "top");
        assert_eq!(small, (860, 0));
        assert_eq!(big, (635, 0));
        assert_eq!(anchor_of(small, (200, 80), "top"), (960, 0));
        assert_eq!(anchor_of(big, (650, 410), "top"), (960, 0));
        // dragged near the left edge, a wide room is pulled back onto the screen
        assert_eq!(fit_at((100, 0), (650, 410), "top", &[work]), Some((0, 0)));
    }

    const WORK: (i32, i32, u32, u32) = (0, 0, 1920, 1032); // 1080p minus a 48 px taskbar
    const SECOND: (i32, i32, u32, u32) = (1920, 0, 2560, 1400);

    #[test]
    fn a_window_is_kept_fully_on_its_screen() {
        assert_eq!(clamp_into((700, 400), (440, 180), WORK), (700, 400));
        assert_eq!(clamp_into((1800, 1000), (440, 180), WORK), (1920 - 440, 1032 - 180));
        assert_eq!(clamp_into((-200, -50), (440, 180), WORK), (0, 0));
        assert_eq!(clamp_into((4400, 10), (440, 180), SECOND), (1920 + 2560 - 440, 10));
    }

    #[test]
    fn the_character_stands_flush_in_its_corner_above_the_taskbar() {
        assert_eq!(default_anchor("corner", "right", WORK, 1.0), (1920, 1032));
        assert_eq!(default_anchor("corner", "left", WORK, 1.0), (0, 1032));
        // edge: partway down the side, never below the work area
        assert_eq!(default_anchor("edge", "right", WORK, 1.0), (1920, (1032.0_f64 * 0.58 + 75.0).round() as i32));
        assert_eq!(default_anchor("edge", "left", (0, 0, 800, 100), 2.0), (0, 100));
        // a style it does not know is the corner
        assert_eq!(default_anchor("island", "right", SECOND, 1.5), (1920 + 2560, 1400));
    }

    #[test]
    fn the_character_does_not_move_when_a_bubble_makes_the_window_grow() {
        // right side: the window grows to the left and up; the bottom-right corner stays
        let small = from_anchor((1920, 1032), (188, 148), "right");
        let big = from_anchor((1920, 1032), (448, 148), "right");
        assert_eq!(small, (1920 - 188, 1032 - 148));
        assert_eq!(big, (1920 - 448, 1032 - 148));
        assert_eq!(anchor_of(small, (188, 148), "right"), anchor_of(big, (448, 148), "right"));
        // left side: it grows to the right; the bottom-left corner stays
        let small = from_anchor((0, 1032), (188, 148), "left");
        let big = from_anchor((0, 1032), (448, 200), "left");
        assert_eq!((small.0, big.0), (0, 0));
        assert_eq!(anchor_of(small, (188, 148), "left"), anchor_of(big, (448, 200), "left"));
    }

    #[test]
    fn a_dragged_place_is_used_while_its_screen_is_there_and_dropped_when_it_is_gone() {
        let both = [WORK, SECOND];
        assert_eq!(work_of((3000, 700), &both), Some(SECOND));
        assert_eq!(work_of((1920, 1032), &[WORK]), Some(WORK), "the very corner of the screen is on it");
        assert_eq!(fit_at((3000, 700), (188, 148), "right", &both), Some((3000 - 188, 700 - 148)));
        assert_eq!(fit_at((3000, 700), (188, 148), "right", &[WORK]), None);
        // an anchor near the left edge with a wide window: pulled back onto the screen
        assert_eq!(fit_at((100, 500), (448, 148), "right", &[WORK]), Some((0, 500 - 148)));
    }

    #[test]
    fn panel_opens_beside_the_buddy_inside_the_screen() {
        // right-side buddy in the corner: the panel is to its left, bottoms aligned, 8 px inside the work area
        let buddy = (1920 - 188, 1032 - 148, 188, 148);
        let (x, y) = panel_place("right", buddy, (380, 540), WORK, 1.0);
        assert_eq!((x, y), (1920 - 188 - 380, 1032 - 540 - 8));
        // left-side buddy: the panel is to its right
        let (x, _) = panel_place("left", (0, 884, 188, 148), (380, 540), WORK, 1.0);
        assert_eq!(x, 188);
        // a right-side buddy dragged to the far left: no room on its left, so the panel opens on its right
        let (x, _) = panel_place("right", (20, 500, 188, 148), (380, 540), WORK, 1.0);
        assert_eq!(x, 20 + 188);
        // near the top: kept inside the work area
        let (_, y) = panel_place("right", (1700, 10, 188, 148), (380, 540), WORK, 1.0);
        assert_eq!(y, 8);
    }
}
