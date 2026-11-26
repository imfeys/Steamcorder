//! Windows integration: tray icon, single instance, start with Windows, small helpers.

use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicPtr, AtomicU32, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;

use windows_sys::Win32::Foundation::{
    CloseHandle, ERROR_PIPE_CONNECTED, GetLastError, HANDLE, HWND, INVALID_HANDLE_VALUE, LPARAM,
    LRESULT, POINT, WPARAM,
};
use windows_sys::Win32::Storage::FileSystem::{
    FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_ACCESS_INBOUND, ReadFile,
};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_REJECT_REMOTE_CLIENTS,
    PIPE_TYPE_BYTE, PIPE_WAIT,
};
use windows_sys::Win32::System::SystemInformation::GetLocalTime;
use windows_sys::Win32::UI::Shell::{
    NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_TIP, NIIF_INFO, NIM_ADD, NIM_DELETE, NIM_MODIFY,
    NOTIFYICONDATAW, SetCurrentProcessExplicitAppUserModelID, Shell_NotifyIconW, ShellExecuteW,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    AllowSetForegroundWindow, AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW,
    DestroyMenu, DispatchMessageW, GetCursorPos, GetMessageW, GetSystemMetrics, HICON,
    IDI_APPLICATION, IMAGE_ICON, LR_DEFAULTCOLOR, LoadIconW, LoadImageW, MF_SEPARATOR, MF_STRING,
    MSG, PostMessageW, RegisterClassW, RegisterWindowMessageW, SM_CXSMICON, SM_CYSMICON,
    SW_SHOWNORMAL, SendMessageW, SetForegroundWindow, SetMenuDefaultItem, TPM_NONOTIFY,
    TPM_RETURNCMD, TPM_RIGHTBUTTON, TrackPopupMenu, TranslateMessage, WM_APP, WM_LBUTTONDBLCLK,
    WM_LBUTTONUP, WM_NULL, WM_RBUTTONUP, WNDCLASSW,
};

use crate::APP_NAME;
use crate::core::Core;

pub fn wide(s: impl AsRef<OsStr>) -> Vec<u16> {
    s.as_ref().encode_wide().chain(std::iter::once(0)).collect()
}

/// Group the app's taskbar button and notifications under its own identity.
pub fn set_app_id() {
    let id = wide(format!("imfeys.{APP_NAME}"));
    // SAFETY: `id` is a valid NUL-terminated wide string that outlives the call.
    unsafe {
        SetCurrentProcessExplicitAppUserModelID(id.as_ptr());
    }
}

/// Local wall-clock time as "HH:MM".
pub fn local_time_hhmm() -> String {
    // SAFETY: GetLocalTime only writes into the struct we pass.
    let t = unsafe {
        let mut t = std::mem::zeroed();
        GetLocalTime(&mut t);
        t
    };
    format!("{:02}:{:02}", t.wHour, t.wMinute)
}

pub fn open_url(url: &str) {
    let (op, file) = (wide("open"), wide(url));
    // SAFETY: all string pointers are valid NUL-terminated wide strings for the call.
    unsafe {
        ShellExecuteW(
            null_mut(),
            op.as_ptr(),
            file.as_ptr(),
            null(),
            null(),
            SW_SHOWNORMAL,
        );
    }
}

// ----------------------------------------------------------------------
// Start with Windows
// ----------------------------------------------------------------------
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// Command line Windows runs at sign-in; starts hidden in the tray.
fn launch_command() -> std::io::Result<String> {
    Ok(format!(
        "\"{}\" --minimized",
        std::env::current_exe()?.display()
    ))
}

/// Add or remove the sign-in entry.
pub fn set_start_with_windows(enabled: bool) -> Result<(), String> {
    use winreg::RegKey;
    use winreg::enums::{HKEY_CURRENT_USER, KEY_SET_VALUE};
    let run = || -> std::io::Result<()> {
        let key =
            RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags(RUN_KEY, KEY_SET_VALUE)?;
        if enabled {
            key.set_value(APP_NAME, &launch_command()?)
        } else {
            match key.delete_value(APP_NAME) {
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
                other => other,
            }
        }
    };
    run().map_err(|e| crate::os_message(&e))
}

// ----------------------------------------------------------------------
// Single instance
// ----------------------------------------------------------------------
// Running twice would upload every screenshot twice. The first instance owns a
// named pipe; later ones connect to it to ask it to show itself, then exit.
// The pipe name matches the one Qt's QLocalServer used in the Python version,
// so old and new builds also recognise each other.

fn pipe_name() -> Vec<u16> {
    let user = std::env::var("USERNAME").unwrap_or_default();
    wide(format!(r"\\.\pipe\{APP_NAME}-{user}"))
}

pub struct InstanceServer(HANDLE);
impl InstanceServer {
    fn raw(&self) -> HANDLE {
        self.0
    }
}

// SAFETY: a pipe handle can be used from any thread; only the listener thread uses it.
unsafe impl Send for InstanceServer {}

/// Become the running instance. Returns None if another instance already owns the pipe.
pub fn claim_instance() -> Option<InstanceServer> {
    let name = pipe_name();
    // SAFETY: `name` is a valid wide string; null security attributes mean defaults.
    let handle = unsafe {
        CreateNamedPipeW(
            name.as_ptr(),
            PIPE_ACCESS_INBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1,
            0,
            64,
            0,
            null(),
        )
    };
    (handle != INVALID_HANDLE_VALUE).then_some(InstanceServer(handle))
}

/// If Steamcorder is already open, ask it to show itself and return true.
pub fn signal_running_instance() -> bool {
    use std::io::Write;
    let name = String::from_utf16_lossy(&pipe_name()[..pipe_name().len() - 1]);
    for _ in 0..3 {
        match std::fs::OpenOptions::new().write(true).open(&name) {
            Ok(mut pipe) => {
                // Let the running instance take the foreground; we have it because the user just launched us.
                // SAFETY: plain Win32 call with a constant argument.
                unsafe { AllowSetForegroundWindow(u32::MAX) };
                let _ = pipe.write_all(b"show");
                return true;
            }
            // ERROR_PIPE_BUSY: the running instance is serving someone else right now.
            Err(e) if e.raw_os_error() == Some(231) => {
                thread::sleep(std::time::Duration::from_millis(100))
            }
            Err(_) => return false,
        }
    }
    false
}

/// Show the window whenever another instance connects.
pub fn listen_for_instances(server: InstanceServer, core: Arc<Core>) {
    thread::Builder::new()
        .name("instance".into())
        .spawn(move || {
            let handle = server.raw();
            loop {
                // SAFETY: `handle` is our open pipe; synchronous calls with no OVERLAPPED.
                let connected = unsafe {
                    ConnectNamedPipe(handle, null_mut()) != 0
                        || GetLastError() == ERROR_PIPE_CONNECTED
                };
                if connected {
                    let mut buf = [0u8; 64];
                    let mut read = 0u32;
                    // SAFETY: `buf` is valid for `buf.len()` bytes.
                    unsafe {
                        ReadFile(
                            handle,
                            buf.as_mut_ptr(),
                            buf.len() as u32,
                            &mut read,
                            null_mut(),
                        );
                    }
                    core.request_show();
                }
                // SAFETY: `handle` is our open pipe.
                if unsafe { DisconnectNamedPipe(handle) } == 0 && !connected {
                    // SAFETY: we own the handle and stop using it.
                    unsafe { CloseHandle(handle) };
                    return;
                }
            }
        })
        .expect("spawn instance listener");
}

// ----------------------------------------------------------------------
// Tray icon
// ----------------------------------------------------------------------
const WM_TRAY: u32 = WM_APP + 1;
const WM_TRAY_REFRESH: u32 = WM_APP + 2;
const WM_TRAY_BALLOON: u32 = WM_APP + 3;
const WM_TRAY_REMOVE: u32 = WM_APP + 4;
const TRAY_ID: u32 = 1;
const CMD_OPEN: usize = 1;
const CMD_TOGGLE: usize = 2;
const CMD_QUIT: usize = 3;

static TRAY_HWND: AtomicPtr<core::ffi::c_void> = AtomicPtr::new(null_mut());
static TASKBAR_CREATED: AtomicU32 = AtomicU32::new(0);
static TRAY_CORE: OnceLock<Arc<Core>> = OnceLock::new();
static BALLOON: Mutex<Option<(String, String)>> = Mutex::new(None);

/// Create the tray icon on its own thread with its own message loop.
pub fn spawn_tray(core: Arc<Core>) {
    let _ = TRAY_CORE.set(core);
    thread::Builder::new()
        .name("tray".into())
        .spawn(|| {
            // SAFETY: standard Win32 window setup; all pointers are valid for each call,
            // and the window procedure only runs on this thread.
            unsafe {
                let hinstance = GetModuleHandleW(null());
                let class = wide("SteamcorderTray");
                let wc = WNDCLASSW {
                    lpfnWndProc: Some(tray_proc),
                    hInstance: hinstance,
                    lpszClassName: class.as_ptr(),
                    ..std::mem::zeroed()
                };
                RegisterClassW(&wc);
                // A hidden top-level window (not message-only) so it hears "TaskbarCreated"
                // and can put the icon back after Explorer restarts.
                let hwnd = CreateWindowExW(
                    0,
                    class.as_ptr(),
                    class.as_ptr(),
                    0,
                    0,
                    0,
                    0,
                    0,
                    null_mut(),
                    null_mut(),
                    hinstance,
                    null(),
                );
                if hwnd.is_null() {
                    return;
                }
                TASKBAR_CREATED.store(
                    RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()),
                    Ordering::SeqCst,
                );
                TRAY_HWND.store(hwnd, Ordering::SeqCst);
                notify_icon(hwnd, NIM_ADD);

                let mut msg: MSG = std::mem::zeroed();
                while GetMessageW(&mut msg, null_mut(), 0, 0) > 0 {
                    TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
            }
        })
        .expect("spawn tray");
}

fn tray_hwnd() -> HWND {
    TRAY_HWND.load(Ordering::SeqCst)
}

/// Update the tooltip after watching starts or stops.
pub fn tray_refresh() {
    let hwnd = tray_hwnd();
    if !hwnd.is_null() {
        // SAFETY: posting to our own window; harmless if it is gone.
        unsafe { PostMessageW(hwnd, WM_TRAY_REFRESH, 0, 0) };
    }
}

/// Show a notification balloon from the tray icon.
pub fn tray_balloon(title: &str, message: &str) {
    *BALLOON.lock().unwrap_or_else(|e| e.into_inner()) = Some((title.into(), message.into()));
    let hwnd = tray_hwnd();
    if !hwnd.is_null() {
        // SAFETY: posting to our own window.
        unsafe { PostMessageW(hwnd, WM_TRAY_BALLOON, 0, 0) };
    }
}

/// Remove the tray icon now (before the process exits).
pub fn tray_remove() {
    let hwnd = tray_hwnd();
    if !hwnd.is_null() {
        // SAFETY: the tray thread never waits on the caller, so a synchronous send can't deadlock.
        unsafe { SendMessageW(hwnd, WM_TRAY_REMOVE, 0, 0) };
    }
}

fn copy_wide(dst: &mut [u16], s: &str) {
    let src: Vec<u16> = s.encode_utf16().take(dst.len() - 1).collect();
    dst[..src.len()].copy_from_slice(&src);
    dst[src.len()] = 0;
}

fn tooltip() -> String {
    let running = TRAY_CORE.get().is_some_and(|c| c.is_running());
    format!(
        "{APP_NAME}: {}",
        if running { "watching" } else { "paused" }
    )
}

unsafe fn load_tray_icon() -> HICON {
    // SAFETY: resource 1 is the app icon embedded by build.rs; fall back to the stock icon.
    unsafe {
        let icon = LoadImageW(
            GetModuleHandleW(null()),
            1 as _,
            IMAGE_ICON,
            GetSystemMetrics(SM_CXSMICON),
            GetSystemMetrics(SM_CYSMICON),
            LR_DEFAULTCOLOR,
        );
        if icon.is_null() {
            LoadIconW(null_mut(), IDI_APPLICATION)
        } else {
            icon as HICON
        }
    }
}

unsafe fn notify_icon(hwnd: HWND, message: u32) {
    // SAFETY: `data` is fully initialised for the fields named in `uFlags`.
    unsafe {
        let mut data: NOTIFYICONDATAW = std::mem::zeroed();
        data.cbSize = size_of::<NOTIFYICONDATAW>() as u32;
        data.hWnd = hwnd;
        data.uID = TRAY_ID;
        if message != NIM_DELETE {
            data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
            data.uCallbackMessage = WM_TRAY;
            data.hIcon = load_tray_icon();
            copy_wide(&mut data.szTip, &tooltip());
        }
        if message == NIM_MODIFY {
            let balloon = BALLOON.lock().unwrap_or_else(|e| e.into_inner()).take();
            if let Some((title, text)) = balloon {
                data.uFlags |= NIF_INFO;
                data.dwInfoFlags = NIIF_INFO;
                copy_wide(&mut data.szInfoTitle, &title);
                copy_wide(&mut data.szInfo, &text);
            }
        }
        Shell_NotifyIconW(message, &data);
    }
}

unsafe fn show_menu(hwnd: HWND) {
    let Some(core) = TRAY_CORE.get() else { return };
    let toggle = if core.is_running() {
        "Stop watching"
    } else {
        "Start watching"
    };
    // SAFETY: the menu is created, shown and destroyed on this thread; strings outlive the calls.
    unsafe {
        let menu = CreatePopupMenu();
        let (open, toggle, quit) = (wide(format!("Open {APP_NAME}")), wide(toggle), wide("Quit"));
        AppendMenuW(menu, MF_STRING, CMD_OPEN, open.as_ptr());
        AppendMenuW(menu, MF_STRING, CMD_TOGGLE, toggle.as_ptr());
        AppendMenuW(menu, MF_SEPARATOR, 0, null());
        AppendMenuW(menu, MF_STRING, CMD_QUIT, quit.as_ptr());
        SetMenuDefaultItem(menu, CMD_OPEN as u32, 0);

        let mut pt = POINT { x: 0, y: 0 };
        GetCursorPos(&mut pt);
        // Required so the menu closes when the user clicks elsewhere.
        SetForegroundWindow(hwnd);
        let cmd = TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            pt.x,
            pt.y,
            0,
            hwnd,
            null(),
        );
        PostMessageW(hwnd, WM_NULL, 0, 0);
        DestroyMenu(menu);

        match cmd as usize {
            CMD_OPEN => core.request_show(),
            CMD_TOGGLE => core.toggle_watching(),
            CMD_QUIT => core.request_quit(),
            _ => {}
        }
    }
}

unsafe extern "system" fn tray_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: called by Windows on the tray thread with a valid `hwnd`.
    unsafe {
        match msg {
            WM_TRAY => {
                match lparam as u32 {
                    WM_LBUTTONUP | WM_LBUTTONDBLCLK => {
                        if let Some(core) = TRAY_CORE.get() {
                            core.request_show();
                        }
                    }
                    WM_RBUTTONUP => show_menu(hwnd),
                    _ => {}
                }
                0
            }
            WM_TRAY_REFRESH | WM_TRAY_BALLOON => {
                notify_icon(hwnd, NIM_MODIFY);
                0
            }
            WM_TRAY_REMOVE => {
                notify_icon(hwnd, NIM_DELETE);
                TRAY_HWND.store(null_mut(), Ordering::SeqCst);
                0
            }
            m if m != 0 && m == TASKBAR_CREATED.load(Ordering::SeqCst) => {
                if !TRAY_HWND.load(Ordering::SeqCst).is_null() {
                    notify_icon(hwnd, NIM_ADD);
                }
                0
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}
