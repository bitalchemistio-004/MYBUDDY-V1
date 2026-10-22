//! Starts the Python backend and keeps it running.
//!
//! The shell attaches to a backend that is already answering (one started by
//! hand, say) instead of starting a second one on the same port. If the
//! backend it started exits, it is restarted with a growing delay so a
//! backend that crashes on start cannot spin the CPU. On quit, only a backend
//! this shell started is stopped.

use std::fs::OpenOptions;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

/// A Windows job with "kill on job close": the backend this shell starts is
/// put in it, and so is everything the backend starts that doesn't leave on
/// purpose (the model server and its runner). When the shell exits, even by
/// crashing or being force-closed, Windows ends the whole job, so nothing is
/// left behind eating memory (U1). Apps opened *for the user* leave the job
/// (the backend's core/launch.py), which is why breakaway is allowed.
mod job {
    use std::ffi::c_void;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::ptr::{null, null_mut};

    type Handle = *mut c_void;

    #[repr(C)]
    #[derive(Default)]
    struct BasicLimit {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: u32,
        minimum_working_set_size: usize,
        maximum_working_set_size: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    #[derive(Default)]
    struct IoCounters([u64; 6]);

    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimit {
        basic: BasicLimit,
        io: IoCounters,
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_used: usize,
        peak_job_memory_used: usize,
    }

    const KILL_ON_JOB_CLOSE: u32 = 0x2000;
    const BREAKAWAY_OK: u32 = 0x0800;
    const EXTENDED_LIMIT_INFORMATION: i32 = 9;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *mut c_void, name: *const u16) -> Handle;
        fn SetInformationJobObject(job: Handle, class: i32, info: *mut c_void, length: u32) -> i32;
        fn AssignProcessToJobObject(job: Handle, process: Handle) -> i32;
        fn TerminateJobObject(job: Handle, exit_code: u32) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
    }

    pub struct Job(Handle);

    // The handle is only used through these calls, which Windows allows from any thread.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new() -> Option<Job> {
            unsafe {
                let handle = CreateJobObjectW(null_mut(), null());
                if handle.is_null() {
                    return None;
                }
                let mut info = ExtendedLimit::default();
                info.basic.limit_flags = KILL_ON_JOB_CLOSE | BREAKAWAY_OK;
                let size = std::mem::size_of::<ExtendedLimit>() as u32;
                if SetInformationJobObject(handle, EXTENDED_LIMIT_INFORMATION, &mut info as *mut _ as *mut c_void, size) == 0 {
                    CloseHandle(handle);
                    return None;
                }
                Some(Job(handle))
            }
        }

        /// Puts a started process in the job (nested jobs work on Windows 8+).
        pub fn assign(&self, child: &Child) -> bool {
            unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as Handle) != 0 }
        }

        /// Ends every process in the job now.
        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.0, 1);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

pub const BACKEND_URL: &str = "http://127.0.0.1:5000";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Delay before restarting after `failures` consecutive exits:
/// 1, 2, 4, 8, 16, then 30 seconds.
pub fn restart_delay(failures: u32) -> Duration {
    let secs = 1u64 << failures.saturating_sub(1).min(5);
    Duration::from_secs(secs.min(30))
}

/// The repository root: `MBUDDY_ROOT` when set, else two levels above this
/// crate (`shell/src-tauri` -> repo).
pub fn repo_root() -> PathBuf {
    if let Ok(root) = std::env::var("MBUDDY_ROOT") {
        return PathBuf::from(root);
    }
    let crate_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let root = crate_dir.join("..").join("..");
    root.canonicalize().unwrap_or(root)
}

fn agent() -> ureq::Agent {
    ureq::AgentBuilder::new().timeout(Duration::from_secs(2)).build()
}

pub fn is_up() -> bool {
    agent().get(&format!("{BACKEND_URL}/")).call().is_ok()
}

/// The current `/status` snapshot, or `None` while the backend is down.
pub fn status() -> Option<serde_json::Value> {
    agent().get(&format!("{BACKEND_URL}/status")).call().ok()?.into_json().ok()
}

/// The app's "Notifications" setting (true when the backend can't be asked,
/// so a reminder is never silently lost).
pub fn notifications_enabled() -> bool {
    agent()
        .get(&format!("{BACKEND_URL}/ui/settings"))
        .call()
        .ok()
        .and_then(|r| r.into_json::<serde_json::Value>().ok())
        .and_then(|v| v.pointer("/settings/notifications").and_then(|b| b.as_bool()))
        .unwrap_or(true)
}

/// The interpreter a virtual environment was made from: `home` in its
/// pyvenv.cfg, when that python.exe exists.
pub fn base_python(venv: &Path) -> Option<PathBuf> {
    let cfg = std::fs::read_to_string(venv.join("pyvenv.cfg")).ok()?;
    let home = home_from_cfg(&cfg)?;
    let exe = PathBuf::from(home).join("python.exe");
    exe.is_file().then_some(exe)
}

fn home_from_cfg(cfg: &str) -> Option<String> {
    cfg.lines().find_map(|line| {
        let (key, value) = line.split_once('=')?;
        (key.trim() == "home" && !value.trim().is_empty()).then(|| value.trim().to_string())
    })
}

pub struct Supervisor {
    root: PathBuf,
    child: Mutex<Option<Child>>,
    stopping: AtomicBool,
    job: Option<job::Job>,
    in_job: AtomicBool,
}

impl Supervisor {
    pub fn new(root: PathBuf) -> Self {
        crate::slog::init(&root);
        let job = job::Job::new();
        if job.is_none() {
            crate::slog!("[shell] could not create a job object; a force-close may leave processes behind");
        }
        Self { root, child: Mutex::new(None), stopping: AtomicBool::new(false), job, in_job: AtomicBool::new(false) }
    }

    fn spawn(&self) -> std::io::Result<Child> {
        let venv_python = self.root.join(".venv").join("Scripts").join("python.exe");
        let logs = self.root.join("logs");
        std::fs::create_dir_all(&logs)?;
        let log = OpenOptions::new().create(true).append(true).open(logs.join("backend.log"))?;
        // The venv's python.exe is a launcher: it starts the real interpreter
        // as a child inside a job of its own, and then whether the backend
        // (and the model server it starts) lands in MBUDDY's job depends on
        // timing. Start the real interpreter directly; this variable makes it
        // behave as the venv's. Without a readable pyvenv.cfg, the launcher.
        let mut command = match base_python(&self.root.join(".venv")) {
            Some(base) => {
                let mut c = Command::new(base);
                c.env("__PYVENV_LAUNCHER__", &venv_python);
                c
            }
            None => Command::new(&venv_python),
        };
        command
            .arg("main.py")
            .current_dir(&self.root)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
    }

    /// Runs forever on its own thread, until `stop` is called.
    pub fn run(&self) {
        let mut failures: u32 = 0;
        while !self.stopping.load(Ordering::SeqCst) {
            let up = is_up();
            let mut guard = self.child.lock().unwrap();
            let mut wait = Duration::from_secs(2);
            match guard.as_mut() {
                Some(child) => match child.try_wait() {
                    Ok(Some(status)) => {
                        crate::slog!("[shell] backend exited: {status}");
                        *guard = None;
                        failures += 1;
                        wait = restart_delay(failures);
                    }
                    Ok(None) if up => failures = 0,
                    _ => {}
                },
                None if !up => match self.spawn() {
                    Ok(child) => {
                        let assigned = self.job.as_ref().map(|j| j.assign(&child)).unwrap_or(false);
                        self.in_job.store(assigned, Ordering::SeqCst);
                        crate::slog!("[shell] started backend pid {} (in MBUDDY's job: {assigned})", child.id());
                        *guard = Some(child);
                    }
                    Err(e) => {
                        crate::slog!("[shell] could not start backend: {e}");
                        failures += 1;
                        wait = restart_delay(failures);
                    }
                },
                None => {} // someone else's backend is answering; attach to it
            }
            drop(guard);
            thread::sleep(wait);
        }
    }

    /// Asks the backend to exit (it may be restarted by `run`).
    pub fn request_shutdown(&self) {
        let _ = agent().post(&format!("{BACKEND_URL}/control/shutdown")).call();
    }

    /// Stops supervising and ends the backend this shell started, if any.
    pub fn stop(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        let mut guard = self.child.lock().unwrap();
        let Some(mut child) = guard.take() else { return };
        self.request_shutdown();
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut exited = false;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                exited = true;
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        // The backend ended by itself (or did not in time): either way end the job, so the model server it started
        // and its runner go with it. (A graceful exit used to leave them running, holding the model in memory.)
        // Without a job, end the backend's process tree as before.
        match (&self.job, self.in_job.load(Ordering::SeqCst)) {
            (Some(job), true) => job.terminate(),
            _ if exited => {}
            _ => {
                let _ = Command::new("taskkill")
                    .args(["/PID", &child.id().to_string(), "/T", "/F"])
                    .creation_flags(CREATE_NO_WINDOW)
                    .status();
            }
        }
        let _ = child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restart_delay_grows_and_caps() {
        let secs: Vec<u64> = (1..=8).map(|f| restart_delay(f).as_secs()).collect();
        assert_eq!(secs, vec![1, 2, 4, 8, 16, 30, 30, 30]);
        assert_eq!(restart_delay(0).as_secs(), 1);
    }

    #[test]
    fn closing_the_job_ends_the_process_in_it() {
        // A harmless child that would run ~30 s on its own.
        let mut child = Command::new("cmd")
            .args(["/c", "ping", "-n", "30", "127.0.0.1"])
            .stdout(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("start a test process");
        let job = job::Job::new().expect("create a job");
        assert!(job.assign(&child), "assign the child to the job");
        drop(job); // what happens when the shell exits or crashes
        let deadline = Instant::now() + Duration::from_secs(2);
        let mut ended = false;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                ended = true;
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
        if !ended {
            let _ = child.kill();
        }
        assert!(ended, "the child should end within 2 s of the job closing");
    }

    #[test]
    fn the_base_interpreter_is_read_from_pyvenv_cfg() {
        let cfg = "home = C:\\Python310\ninclude-system-site-packages = false\nversion = 3.10.0\n";
        assert_eq!(home_from_cfg(cfg).as_deref(), Some("C:\\Python310"));
        assert_eq!(home_from_cfg("version = 3.10.0\n"), None);
        assert_eq!(home_from_cfg("home =\n"), None);
        // This repository's own environment resolves to a real python.exe.
        let base = base_python(&repo_root().join(".venv"));
        assert!(base.map(|p| p.is_file()).unwrap_or(false));
    }

    #[test]
    fn repo_root_contains_the_backend_entry_point() {
        assert!(repo_root().join("main.py").is_file());
    }
}
