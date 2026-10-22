//! The voice bridge: the laptop mic and speakers, inside the shell.
//!
//! `ui/listener.html` runs in a hidden WebView2 window. Chromium's WebRTC
//! capture is the clean path on this laptop (raw Windows capture of the
//! Intel array was quiet and robotic, docs/audit/AUDIT_LOG.md 2.2). Until
//! now the page ran in a Chrome tab; here it needs no tab, no click and no
//! permission prompt nobody could see in a hidden window.

use std::path::Path;

use tauri::{App, WebviewUrl, WebviewWindowBuilder};

/// `MBUDDY_DEVICE_TOKEN` from the repo's `.env`, if set there.
pub fn device_token(root: &Path) -> Option<String> {
    let text = std::fs::read_to_string(root.join(".env")).ok()?;
    token_from_env_text(&text)
}

fn token_from_env_text(text: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let value = line.trim().strip_prefix("MBUDDY_DEVICE_TOKEN=")?;
        let value = value.trim().trim_matches(|c| c == '"' || c == '\'');
        (!value.is_empty()).then(|| value.to_string())
    })
}

/// Chromium flags for the hidden bridge window.
/// - auto-accept the mic prompt (it would be invisible in a hidden window);
/// - let audio start without a click;
/// - don't throttle a page that is never visible.
///
/// Tauri's own default flags are kept; setting any flags replaces them.
/// Every WebView2 window in the app must use the same flags (they share one
/// browser environment; a window with different flags silently fails to start),
/// so all windows take them from `browser_args()`.
pub const BROWSER_ARGS: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection \
--use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required \
--disable-background-timer-throttling --disable-renderer-backgrounding \
--disable-backgrounding-occluded-windows";

/// The flags for every window. MBUDDY_DEVTOOLS=1 adds a local DevTools port
/// (127.0.0.1:9333) for testing; never on by default.
pub fn browser_args() -> String {
    let mut args = BROWSER_ARGS.to_string();
    if std::env::var("MBUDDY_DEVTOOLS").as_deref() == Ok("1") {
        args.push_str(" --remote-debugging-port=9333");
    }
    args
}

pub fn open_bridge(app: &App, root: &Path) -> tauri::Result<()> {
    // The token goes in before the page's script runs, not in the URL, so it
    // never shows up in a log line. Without one the page uses the backend's
    // local default, same as before.
    let init = match device_token(root) {
        Some(token) => format!("window.__MBUDDY_HOST = \"app\"; window.__MBUDDY_TOKEN = {};", serde_json::to_string(&token).unwrap_or_default()),
        None => "window.__MBUDDY_HOST = \"app\";".to_string(),
    };
    WebviewWindowBuilder::new(app, "voice", WebviewUrl::App("listener.html".into()))
        .title("MBUDDY voice bridge")
        .visible(false)
        .skip_taskbar(true)
        .additional_browser_args(&browser_args())
        .initialization_script(&init)
        .build()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::token_from_env_text;

    #[test]
    fn windows_are_created_in_code_with_one_flag_set() {
        // A window defined in tauri.conf.json would get different flags.
        let conf: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(conf.pointer("/app/windows").and_then(|w| w.as_array()).map(|a| a.len()), Some(0));
        assert!(super::browser_args().starts_with(super::BROWSER_ARGS));
    }

    #[test]
    fn reads_the_token_line_only() {
        let env = "GROQ_API_KEY=x\nMBUDDY_DEVICE_TOKEN=\"abc123\"\nOTHER=1\n";
        assert_eq!(token_from_env_text(env).as_deref(), Some("abc123"));
        assert_eq!(token_from_env_text("MBUDDY_DEVICE_TOKEN=\n"), None);
        assert_eq!(token_from_env_text("NOPE=1"), None);
    }
}
