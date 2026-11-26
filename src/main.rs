//! Steamcorder: automatically post new Steam screenshots to a Discord webhook.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod core;
mod discord;
mod settings;
mod ui;
mod win;

use settings::Settings;

pub const APP_NAME: &str = "Steamcorder";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const DISCORD_INVITE: &str = "https://discord.gg/dMvCH93sYX";
pub const MAX_DELAY_SECONDS: u32 = 30;
pub const MAX_LOG_ENTRIES: usize = 200;

/// The OS error text without Rust's " (os error N)" suffix, like Python's `strerror`.
pub fn os_message(e: &std::io::Error) -> String {
    let text = e.to_string();
    match text.rfind(" (os error ") {
        Some(i) if text.ends_with(')') => text[..i].to_owned(),
        _ => text,
    }
}

fn main() -> eframe::Result {
    win::set_app_id();

    // Running twice would upload every screenshot twice.
    let instance = win::claim_instance();
    if instance.is_none() && win::signal_running_instance() {
        return Ok(());
    }

    let settings = Settings::load();
    let start_hidden = std::env::args().any(|a| a == "--minimized")
        || (settings.minimize_on_exit && settings.monitoring_active);

    let core = core::Core::new(settings.clone());
    if let Some(server) = instance {
        win::listen_for_instances(server, core.clone());
    }
    win::spawn_tray(core.clone());

    if settings.start_on_startup {
        // Keep the entry pointing at wherever the app lives now.
        let _ = win::set_start_with_windows(true);
    }
    if settings.monitoring_active && settings.folder_ok() && settings.webhook_ok() {
        core.start_watching();
    }

    ui::run(core, start_hidden)
}

#[cfg(test)]
mod tests {
    use super::os_message;

    #[test]
    fn os_message_strips_error_code() {
        let e = std::io::Error::from_raw_os_error(2);
        let text = os_message(&e);
        assert!(!text.contains("os error"), "{text}");
        assert!(!text.is_empty());
    }
}
