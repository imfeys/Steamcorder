//! App state shared by the window, the tray and the upload workers.

use std::collections::{HashSet, VecDeque};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, Weak, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use notify::event::{ModifyKind, RenameMode};
use notify::{EventKind, RecommendedWatcher, RecursiveMode, Watcher};

use crate::discord::{self, PostError};
use crate::settings::{Settings, is_webhook_url};
use crate::{MAX_LOG_ENTRIES, os_message, win};

const IMAGE_EXTENSIONS: [&str; 6] = ["png", "jpg", "jpeg", "gif", "bmp", "webp"];
const UPLOAD_WORKERS: usize = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Info,
    Success,
    Warning,
    Error,
}

#[derive(Debug, Clone)]
pub struct LogEntry {
    pub level: Level,
    pub message: String,
    pub time: String,
}

/// Which setting the user changed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Changed {
    WatchDirectory,
    WebhookUrl,
    UploadDelay,
    DeleteAfterUpload,
    MinimizeOnExit,
    StartOnStartup,
}

pub struct State {
    pub settings: Settings,
    /// Newest first.
    pub log: VecDeque<LogEntry>,
    pub uploads: u32,
    pub test_result: Option<(bool, String)>,
    /// Requests from the tray or a second instance, handled by the window.
    pub request_show: bool,
    pub request_settings_page: bool,
    pub request_quit: bool,
    watcher: Option<RecommendedWatcher>,
}

enum Job {
    Upload(PathBuf),
    Test(String),
}

pub struct Core {
    state: Mutex<State>,
    running: AtomicBool,
    in_progress: Mutex<HashSet<PathBuf>>,
    jobs: mpsc::Sender<Job>,
    ctx: OnceLock<egui::Context>,
}

impl Core {
    pub fn new(settings: Settings) -> Arc<Self> {
        let (tx, rx) = mpsc::channel::<Job>();
        let core = Arc::new(Self {
            state: Mutex::new(State {
                settings,
                log: VecDeque::new(),
                uploads: 0,
                test_result: None,
                request_show: false,
                request_settings_page: false,
                request_quit: false,
                watcher: None,
            }),
            running: AtomicBool::new(false),
            in_progress: Mutex::new(HashSet::new()),
            jobs: tx,
            ctx: OnceLock::new(),
        });
        let rx = Arc::new(Mutex::new(rx));
        for i in 0..UPLOAD_WORKERS {
            let rx = Arc::clone(&rx);
            let core = Arc::downgrade(&core);
            thread::Builder::new()
                .name(format!("upload-{i}"))
                .spawn(move || worker(&core, &rx))
                .expect("spawn upload worker");
        }
        core
    }

    /// Lets background threads wake the window.
    pub fn set_context(&self, ctx: egui::Context) {
        let _ = self.ctx.set(ctx);
    }

    pub fn repaint(&self) {
        if let Some(ctx) = self.ctx.get() {
            ctx.request_repaint();
        }
    }

    pub fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn settings(&self) -> Settings {
        self.lock().settings.clone()
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    pub fn log(&self, level: Level, message: impl Into<String>) {
        let entry = LogEntry {
            level,
            message: message.into(),
            time: win::local_time_hhmm(),
        };
        {
            let mut st = self.lock();
            st.log.push_front(entry);
            st.log.truncate(MAX_LOG_ENTRIES);
        }
        self.repaint();
    }

    pub fn clear_log(&self) {
        self.lock().log.clear();
    }

    /// Ask the window to come to the front (from the tray or a second instance).
    pub fn request_show(&self) {
        self.lock().request_show = true;
        self.repaint();
    }

    pub fn request_quit(&self) {
        self.lock().request_quit = true;
        self.repaint();
    }

    // --- watching ---------------------------------------------------------

    pub fn toggle_watching(self: &Arc<Self>) {
        if self.is_running() {
            self.stop_watching();
        } else {
            self.start_watching();
        }
    }

    pub fn start_watching(self: &Arc<Self>) {
        let settings = self.settings();
        if !(settings.folder_ok() && settings.webhook_ok()) {
            {
                let mut st = self.lock();
                st.request_settings_page = true;
                st.request_show = true;
            }
            self.repaint();
            return;
        }
        let folder = settings.watch_directory;
        match self.make_watcher(Path::new(&folder)) {
            Ok(watcher) => {
                self.lock().watcher = Some(watcher);
                self.running.store(true, Ordering::SeqCst);
                self.log(Level::Info, format!("Watching {folder}"));
                self.set_active(true);
            }
            Err(message) => self.log(Level::Error, format!("Couldn't watch {folder}: {message}")),
        }
    }

    pub fn stop_watching(&self) {
        self.stop_quietly();
        self.log(Level::Info, "Stopped watching");
        self.set_active(false);
    }

    fn stop_quietly(&self) {
        self.running.store(false, Ordering::SeqCst);
        let watcher = self.lock().watcher.take();
        drop(watcher);
        win::tray_refresh();
    }

    /// Stop everything before the app exits. Unlike `stop_watching`, this keeps
    /// `monitoring_active` so watching resumes on the next launch.
    pub fn shutdown(&self) {
        self.stop_quietly();
    }

    fn make_watcher(self: &Arc<Self>, folder: &Path) -> Result<RecommendedWatcher, String> {
        let core = Arc::downgrade(self);
        let mut watcher = notify::recommended_watcher(move |res: notify::Result<notify::Event>| {
            let (Ok(event), Some(core)) = (res, core.upgrade()) else {
                return;
            };
            for path in new_file_paths(&event) {
                core.queue(path);
            }
        })
        .map_err(|e| notify_message(&e))?;
        watcher
            .watch(folder, RecursiveMode::NonRecursive)
            .map_err(|e| notify_message(&e))?;
        Ok(watcher)
    }

    fn set_active(&self, active: bool) {
        self.lock().settings.monitoring_active = active;
        self.save();
        win::tray_refresh();
        self.repaint();
    }

    pub fn save(&self) {
        let settings = self.settings();
        if let Err(e) = settings.save() {
            self.log(Level::Error, format!("Couldn't save settings: {e}"));
        }
    }

    /// React to a setting the window already changed in memory, then save.
    pub fn setting_changed(self: &Arc<Self>, which: Changed) {
        if which == Changed::StartOnStartup {
            let enabled = self.lock().settings.start_on_startup;
            if let Err(e) = win::set_start_with_windows(enabled) {
                self.log(
                    Level::Error,
                    format!("Couldn't update Windows startup: {e}"),
                );
            }
        }
        if which == Changed::WatchDirectory && self.is_running() {
            self.stop_watching();
            self.start_watching(); // pick up the new folder
        }
        self.save();
        self.repaint();
    }

    pub fn test_webhook(&self, url: String) {
        let _ = self.jobs.send(Job::Test(url));
    }

    // --- uploading --------------------------------------------------------

    fn queue(&self, path: PathBuf) {
        if !is_image(&path) || path.is_dir() {
            return;
        }
        {
            let mut busy = self.in_progress.lock().unwrap_or_else(|e| e.into_inner());
            if !busy.insert(path.clone()) {
                return;
            }
        }
        let _ = self.jobs.send(Job::Upload(path));
    }

    fn process(&self, path: &Path) {
        let name = file_name(path);
        if !wait_until_stable(path, Duration::from_secs(60), Duration::from_millis(500)) {
            self.log(
                Level::Warning,
                format!("Skipped {name}: the file disappeared or never finished saving"),
            );
            return;
        }
        let delay = self.lock().settings.upload_delay;
        if delay > 0 {
            thread::sleep(Duration::from_secs(u64::from(delay)));
        }
        if self.is_running() {
            self.upload(path, &name);
        }
    }

    fn upload(&self, path: &Path, name: &str) {
        let url = self.lock().settings.webhook_url.trim().to_owned();
        if !is_webhook_url(&url) {
            self.log(
                Level::Error,
                format!("Couldn't upload {name}: no valid webhook URL in Settings"),
            );
            return;
        }
        let size_mb = match std::fs::metadata(path) {
            Ok(meta) => meta.len() as f64 / 1_048_576.0,
            Err(e) => {
                self.log(
                    Level::Error,
                    format!("Couldn't upload {name}: {}", os_message(&e)),
                );
                return;
            }
        };

        let started = Instant::now();
        let status = match discord::post_file(&url, path) {
            Ok(status) => status,
            Err(PostError::Unreachable) => {
                self.log(
                    Level::Error,
                    format!("Couldn't upload {name}: Discord is unreachable"),
                );
                return;
            }
            Err(PostError::File(e)) => {
                self.log(
                    Level::Error,
                    format!("Couldn't upload {name}: {}", os_message(&e)),
                );
                return;
            }
        };
        let elapsed = started.elapsed().as_secs_f64();

        if status.as_u16() == 413 {
            self.log(
                Level::Error,
                format!("{name} is too large for this Discord server ({size_mb:.1} MB)"),
            );
            return;
        }
        if status.as_u16() >= 400 {
            self.log(
                Level::Error,
                format!("Discord rejected {name} (HTTP {})", status.as_u16()),
            );
            return;
        }

        self.log(
            Level::Success,
            format!("Uploaded {name} · {size_mb:.1} MB in {elapsed:.1}s"),
        );
        let delete = {
            let mut st = self.lock();
            st.uploads += 1;
            st.settings.delete_after_upload
        };
        self.repaint();

        if delete && let Err(e) = std::fs::remove_file(path) {
            self.log(
                Level::Warning,
                format!("Uploaded {name} but couldn't delete it: {}", os_message(&e)),
            );
        }
    }
}

fn worker(core: &Weak<Core>, rx: &Mutex<mpsc::Receiver<Job>>) {
    loop {
        let job = {
            let rx = rx.lock().unwrap_or_else(|e| e.into_inner());
            match rx.recv() {
                Ok(job) => job,
                Err(_) => return,
            }
        };
        let Some(core) = core.upgrade() else { return };
        match job {
            Job::Upload(path) => {
                // A worker must never die silently.
                if catch_unwind(AssertUnwindSafe(|| core.process(&path))).is_err() {
                    core.log(
                        Level::Error,
                        format!("Couldn't upload {}: internal error", file_name(&path)),
                    );
                }
                core.in_progress
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .remove(&path);
            }
            Job::Test(url) => {
                let result = discord::test_webhook(&url);
                core.lock().test_result = Some(result);
                core.repaint();
            }
        }
    }
}

/// Paths of files that just appeared: created, or renamed into place by tools
/// that write to a temp name first.
fn new_file_paths(event: &notify::Event) -> Vec<PathBuf> {
    match event.kind {
        EventKind::Create(_) | EventKind::Modify(ModifyKind::Name(RenameMode::To)) => {
            event.paths.clone()
        }
        EventKind::Modify(ModifyKind::Name(RenameMode::Both)) => {
            event.paths.last().cloned().into_iter().collect()
        }
        _ => Vec::new(),
    }
}

fn notify_message(e: &notify::Error) -> String {
    match &e.kind {
        notify::ErrorKind::Io(io) => os_message(io),
        _ => e.to_string(),
    }
}

pub fn is_image(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| IMAGE_EXTENSIONS.contains(&e.to_ascii_lowercase().as_str()))
}

fn file_name(path: &Path) -> String {
    path.file_name().map_or_else(
        || path.display().to_string(),
        |n| n.to_string_lossy().into_owned(),
    )
}

/// Wait until a file stops growing so a half-written screenshot is never uploaded.
pub fn wait_until_stable(path: &Path, timeout: Duration, interval: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    let mut last_size: Option<u64> = None;
    let mut stable_checks = 0;
    while Instant::now() < deadline {
        let Ok(size) = std::fs::metadata(path).map(|m| m.len()) else {
            return false;
        };
        if size > 0 && Some(size) == last_size {
            stable_checks += 1;
            if stable_checks >= 2 {
                return true;
            }
        } else {
            stable_checks = 0;
        }
        last_size = Some(size);
        thread::sleep(interval);
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn image_extensions() {
        for name in ["a.png", "b.JPG", "c.jpeg", "d.gif", "e.Bmp", "f.webp"] {
            assert!(is_image(Path::new(name)), "{name}");
        }
        for name in ["a.txt", "b.png.tmp", "noext", "c.vdf"] {
            assert!(!is_image(Path::new(name)), "{name}");
        }
    }

    #[test]
    fn stable_file_is_detected() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("shot.png");
        std::fs::write(&path, b"data").expect("write");
        assert!(wait_until_stable(
            &path,
            Duration::from_secs(2),
            Duration::from_millis(10)
        ));
    }

    #[test]
    fn missing_or_empty_file_is_not_stable() {
        let dir = tempfile::tempdir().expect("tempdir");
        assert!(!wait_until_stable(
            &dir.path().join("gone.png"),
            Duration::from_secs(1),
            Duration::from_millis(10)
        ));
        let empty = dir.path().join("empty.png");
        std::fs::write(&empty, b"").expect("write");
        assert!(!wait_until_stable(
            &empty,
            Duration::from_millis(100),
            Duration::from_millis(10)
        ));
    }

    #[test]
    fn rename_events_use_the_new_name() {
        let event = notify::Event::new(EventKind::Modify(ModifyKind::Name(RenameMode::Both)))
            .add_path(PathBuf::from("a.tmp"))
            .add_path(PathBuf::from("a.png"));
        assert_eq!(new_file_paths(&event), vec![PathBuf::from("a.png")]);
        let removed = notify::Event::new(EventKind::Remove(notify::event::RemoveKind::Any))
            .add_path("x.png".into());
        assert!(new_file_paths(&removed).is_empty());
    }
}
