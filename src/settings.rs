//! User settings, stored as JSON in `%APPDATA%\Steamcorder\config.json`.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use regex::Regex;
use serde::Serialize;
use serde_json::Value;

use crate::{APP_NAME, MAX_DELAY_SECONDS};

static WEBHOOK_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[\w-]+/?(?:\?.*)?$",
    )
    .expect("webhook pattern is valid")
});

/// True if `url` (already trimmed) looks like a Discord webhook URL.
pub fn is_webhook_url(url: &str) -> bool {
    WEBHOOK_PATTERN.is_match(url)
}

const DEFAULT_UPLOAD_DELAY: u32 = 2;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Settings {
    pub watch_directory: String,
    pub webhook_url: String,
    pub upload_delay: u32,
    pub delete_after_upload: bool,
    pub minimize_on_exit: bool,
    pub start_on_startup: bool,
    pub monitoring_active: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            watch_directory: String::new(),
            webhook_url: String::new(),
            upload_delay: DEFAULT_UPLOAD_DELAY,
            delete_after_upload: false,
            minimize_on_exit: false,
            start_on_startup: false,
            monitoring_active: false,
        }
    }
}

/// Per-user folder for settings. Never next to the exe, which may live in a read-only place.
pub fn data_dir() -> PathBuf {
    match std::env::var_os("APPDATA") {
        Some(appdata) if !appdata.is_empty() => PathBuf::from(appdata).join(APP_NAME),
        _ => home_dir().join(".config").join(APP_NAME),
    }
}

pub fn home_dir() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

pub fn config_path() -> PathBuf {
    data_dir().join("config.json")
}

/// Versions before 1.0 kept config.json next to the program; it is still read as a fallback.
pub fn legacy_config_path() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    Some(exe.parent()?.join("config.json"))
}

impl Settings {
    /// Load from the config file, falling back to the legacy file and then to defaults.
    pub fn load() -> Self {
        let mut paths = vec![config_path()];
        paths.extend(legacy_config_path());
        Self::load_from(&paths)
    }

    pub fn load_from(paths: &[PathBuf]) -> Self {
        for path in paths {
            let Ok(text) = fs::read_to_string(path) else {
                continue;
            };
            let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) else {
                continue;
            };
            return Self::from_json(&map);
        }
        Self::default()
    }

    /// Build settings from a JSON object, keeping defaults for missing or unusable values.
    fn from_json(map: &serde_json::Map<String, Value>) -> Self {
        let d = Self::default();
        let string = |key: &str, default: String| match map.get(key) {
            Some(Value::String(s)) => s.clone(),
            _ => default,
        };
        let flag = |key: &str, default: bool| map.get(key).map_or(default, truthy);
        Self {
            watch_directory: string("watch_directory", d.watch_directory),
            webhook_url: string("webhook_url", d.webhook_url),
            upload_delay: map
                .get("upload_delay")
                .and_then(delay_from_json)
                .unwrap_or(d.upload_delay),
            delete_after_upload: flag("delete_after_upload", d.delete_after_upload),
            minimize_on_exit: flag("minimize_on_exit", d.minimize_on_exit),
            start_on_startup: flag("start_on_startup", d.start_on_startup),
            monitoring_active: flag("monitoring_active", d.monitoring_active),
        }
    }

    /// Write settings atomically: to a temp file first, then renamed over the real one.
    pub fn save(&self) -> Result<(), String> {
        self.save_to(&config_path())
    }

    pub fn save_to(&self, path: &Path) -> Result<(), String> {
        let json = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| crate::os_message(&e))?;
        }
        let tmp = path.with_extension("tmp");
        fs::write(&tmp, json).map_err(|e| crate::os_message(&e))?;
        fs::rename(&tmp, path).map_err(|e| crate::os_message(&e))
    }

    pub fn folder_ok(&self) -> bool {
        !self.watch_directory.is_empty() && Path::new(&self.watch_directory).is_dir()
    }

    pub fn webhook_ok(&self) -> bool {
        is_webhook_url(self.webhook_url.trim())
    }
}

/// JSON truthiness, the way the Python version treated hand-edited values.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Read `upload_delay` like Python's `int()`, clamped to 0..=MAX_DELAY_SECONDS.
/// Returns None for values `int()` rejects, so the default is used.
fn delay_from_json(value: &Value) -> Option<u32> {
    let n: i128 = match value {
        Value::Bool(b) => i128::from(*b),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i128::from(i)
            } else if let Some(u) = n.as_u64() {
                i128::from(u)
            } else {
                // int() truncates floats toward zero.
                let f = n.as_f64()?.trunc();
                if f.is_finite() {
                    f.clamp(-1e30, 1e30) as i128
                } else {
                    return None;
                }
            }
        }
        Value::String(s) => s.trim().replace('_', "").parse::<i128>().ok()?,
        _ => return None,
    };
    Some(n.clamp(0, i128::from(MAX_DELAY_SECONDS)) as u32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn parse(v: Value) -> Settings {
        Settings::from_json(v.as_object().expect("object"))
    }

    #[test]
    fn webhook_urls() {
        let good = [
            "https://discord.com/api/webhooks/123/abc-DEF_9",
            "https://discordapp.com/api/webhooks/123/abc",
            "https://ptb.discord.com/api/v10/webhooks/123/abc/",
            "https://canary.discord.com/api/webhooks/1/x?wait=true",
        ];
        let bad = [
            "",
            "http://discord.com/api/webhooks/123/abc",
            "https://discord.com/api/webhooks/abc/abc",
            "https://evil.com/api/webhooks/123/abc",
            "https://discord.com/api/webhooks/123/",
            "https://discord.com.evil.com/api/webhooks/123/abc",
            "https://discord.com/api/webhooks/123/abc def",
        ];
        for url in good {
            assert!(is_webhook_url(url), "{url}");
        }
        for url in bad {
            assert!(!is_webhook_url(url), "{url}");
        }
    }

    #[test]
    fn webhook_ok_trims() {
        let s = Settings {
            webhook_url: "  https://discord.com/api/webhooks/1/a  ".into(),
            ..Settings::default()
        };
        assert!(s.webhook_ok());
    }

    #[test]
    fn delay_is_clamped_and_coerced() {
        assert_eq!(parse(json!({"upload_delay": 5})).upload_delay, 5);
        assert_eq!(parse(json!({"upload_delay": 99})).upload_delay, 30);
        assert_eq!(parse(json!({"upload_delay": -4})).upload_delay, 0);
        assert_eq!(parse(json!({"upload_delay": 7.9})).upload_delay, 7);
        assert_eq!(parse(json!({"upload_delay": " 12 "})).upload_delay, 12);
        assert_eq!(parse(json!({"upload_delay": true})).upload_delay, 1);
        assert_eq!(parse(json!({"upload_delay": "abc"})).upload_delay, 2);
        assert_eq!(parse(json!({"upload_delay": "1.5"})).upload_delay, 2);
        assert_eq!(parse(json!({"upload_delay": null})).upload_delay, 2);
    }

    #[test]
    fn unknown_keys_ignored_and_missing_keys_default() {
        let s = parse(json!({"watch_directory": "C:\\shots", "extra": 1, "minimize_on_exit": 1}));
        assert_eq!(s.watch_directory, "C:\\shots");
        assert!(s.minimize_on_exit);
        assert_eq!(s.upload_delay, 2);
        assert!(!s.monitoring_active);
    }

    #[test]
    fn load_falls_back_to_legacy_then_defaults() {
        let dir = tempfile::tempdir().expect("tempdir");
        let main = dir.path().join("main.json");
        let legacy = dir.path().join("legacy.json");
        fs::write(&legacy, r#"{"webhook_url": "x"}"#).expect("write");
        assert_eq!(
            Settings::load_from(&[main.clone(), legacy.clone()]).webhook_url,
            "x"
        );

        fs::write(&main, "not json").expect("write");
        assert_eq!(
            Settings::load_from(&[main.clone(), legacy.clone()]).webhook_url,
            "x"
        );

        fs::write(&main, "[1, 2]").expect("write");
        assert_eq!(
            Settings::load_from(&[main.clone(), legacy]).webhook_url,
            "x"
        );

        assert_eq!(
            Settings::load_from(&[dir.path().join("none.json")]),
            Settings::default()
        );
    }

    #[test]
    fn save_round_trips() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("sub").join("config.json");
        let s = Settings {
            watch_directory: "D:\\Steam\\screenshots".into(),
            webhook_url: "https://discord.com/api/webhooks/1/a".into(),
            upload_delay: 9,
            delete_after_upload: true,
            minimize_on_exit: true,
            start_on_startup: false,
            monitoring_active: true,
        };
        s.save_to(&path).expect("save");
        assert!(!path.with_extension("tmp").exists());
        assert_eq!(Settings::load_from(&[path]), s);
    }
}
