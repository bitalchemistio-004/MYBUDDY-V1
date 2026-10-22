//! The shell's own log: `logs/shell.log` in the MBUDDY folder. The shell has no console window, so what used to be
//! printed to one ("started backend pid ...") is written here, one line each with the time.
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

static FILE: OnceLock<PathBuf> = OnceLock::new();

/// Where the log goes; called once when the shell knows MBUDDY's folder.
pub fn init(root: &std::path::Path) {
    let _ = FILE.set(root.join("logs").join("shell.log"));
}

pub fn line(text: &str) {
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let Some(path) = FILE.get() else { return };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "[{secs}] {text}");
    }
}

#[macro_export]
macro_rules! slog {
    ($($arg:tt)*) => { $crate::slog::line(&format!($($arg)*)) };
}
