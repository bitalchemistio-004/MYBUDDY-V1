//! MBUDDY desktop shell.
//!
//! The main window (the app: `ui/app.html`), a tray icon, Windows
//! notifications for reminders the backend reports as due, the hidden voice
//! bridge window, and starting/supervising the Python backend. Closing the
//! app window destroys it (a hidden WebView2 window keeps rendering, because
//! the voice bridge's no-throttling flags apply to every window); the buddy,
//! the voice and the backend keep running, and reopening builds the window
//! fresh from the backend's state. Quit is in the tray menu.

mod backend;
mod buddy;
pub mod slog;
mod reminders;
mod voice;

use std::sync::Arc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_notification::NotificationExt;

use backend::Supervisor;
use reminders::{parse_fired, ReminderTracker};

/// Launched by Windows at sign-in: start quietly in the tray.
const AUTOSTART_ARG: &str = "--autostart";

fn now_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// The app window (created in code, not in tauri.conf.json, so it shares the
/// voice and buddy windows' browser flags).
fn create_main(app: &AppHandle) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("app.html".into()))
        .title("MBUDDY")
        .inner_size(1240.0, 820.0)
        .min_inner_size(980.0, 660.0)
        .center()
        .decorations(false)
        .additional_browser_args(&voice::browser_args())
        .build()?;
    Ok(())
}

/// Bring the app window forward, building it again if it was closed.
fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    } else if let Err(e) = create_main(app) {
        crate::slog!("[shell] app window failed to open: {e}");
    }
}

/// The buddy was clicked: open the app.
#[tauri::command]
fn open_main(app: AppHandle) {
    show_main(&app);
}

/// The app's "Start with Windows" switch.
#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let launcher = app.autolaunch();
    let result = if enabled { launcher.enable() } else { launcher.disable() };
    result.map_err(|e| e.to_string())?;
    launcher.is_enabled().map_err(|e| e.to_string())
}

pub fn run() {
    let supervisor = Arc::new(Supervisor::new(backend::repo_root()));

    tauri::Builder::default()
        // A second launch brings the running app forward; one shell owns the backend.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![AUTOSTART_ARG])))
        .invoke_handler(tauri::generate_handler![set_autostart, open_main, buddy::buddy_hot, buddy::buddy_place, buddy::buddy_fit, buddy::open_panel])
        .manage(supervisor.clone())
        .setup(move |app| {
            let open = MenuItem::with_id(app, "open", "Open MBUDDY", true, None::<&str>)?;
            let status_item = MenuItem::with_id(app, "status", "Backend: starting…", false, None::<&str>)?;
            let restart = MenuItem::with_id(app, "restart", "Restart backend", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit MBUDDY", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &status_item, &restart, &quit])?;

            let tray = TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().cloned().expect("bundle icon missing"))
                .tooltip("MBUDDY")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        show_main(tray.app_handle());
                    }
                })
                .on_menu_event(|app, event| {
                    let sup = app.state::<Arc<Supervisor>>().inner().clone();
                    match event.id.as_ref() {
                        "open" => show_main(app),
                        "restart" => {
                            thread::spawn(move || sup.request_shutdown());
                        }
                        "quit" => {
                            sup.stop();
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .build(app)?;

            // Started by Windows at sign-in: no app window until it is opened.
            if !std::env::args().any(|a| a == AUTOSTART_ARG) {
                create_main(app.handle())?;
            }

            let sup = supervisor.clone();
            thread::spawn(move || sup.run());

            // The mic and speakers. It reconnects by itself while the
            // backend starts, so it can open straight away.
            if std::env::var("MBUDDY_NO_VOICE_BRIDGE").is_err() {
                if let Err(e) = voice::open_bridge(app, &backend::repo_root()) {
                    crate::slog!("[shell] voice bridge failed to open: {e}");
                }
            }

            // The pop-up buddy on the desktop (it shows itself once placed).
            if let Err(e) = buddy::open(app) {
                crate::slog!("[shell] buddy window failed to open: {e}");
            }

            // Poll the backend's status: tray state, and a notification for
            // each reminder it has just claimed as due (if the user wants them).
            let handle = app.handle().clone();
            thread::spawn(move || {
                let mut tracker = ReminderTracker::new(now_secs() - 5.0);
                let mut last_up: Option<bool> = None;
                loop {
                    let status = backend::status();
                    let up = status.is_some();
                    if last_up != Some(up) {
                        let text = if up { "Backend: running" } else { "Backend: offline" };
                        let _ = status_item.set_text(text);
                        let _ = tray.set_tooltip(Some(format!("MBUDDY — {}", text.to_lowercase())));
                        last_up = Some(up);
                    }
                    if let Some(status) = status {
                        let due = tracker.take_new(&parse_fired(&status));
                        if !due.is_empty() && backend::notifications_enabled() {
                            for r in due {
                                if let Err(e) = handle.notification().builder().title("MBUDDY").body(&r.title).show() {
                                    crate::slog!("[shell] notification failed for {}: {e}", r.id);
                                }
                            }
                        }
                    }
                    thread::sleep(Duration::from_secs(5));
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building MBUDDY shell")
        .run(|app, event| match event {
            // Closing the app window is not quitting: MBUDDY keeps running
            // (buddy, voice, reminders). Only Quit (an explicit exit code) ends it.
            RunEvent::ExitRequested { code: None, api, .. } => api.prevent_exit(),
            RunEvent::Exit => app.state::<Arc<Supervisor>>().stop(),
            _ => {}
        });
}
