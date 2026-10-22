// No console window, in release or debug builds: the shell writes what it does to logs/shell.log instead (slog.rs).
#![cfg_attr(windows, windows_subsystem = "windows")]

fn main() {
    mbuddy_shell_lib::run()
}
