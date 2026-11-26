//! Posting to a Discord webhook.

use std::path::Path;
use std::sync::LazyLock;
use std::thread;
use std::time::Duration;

use reqwest::StatusCode;
use reqwest::blocking::{Client, Response, multipart};
use serde_json::Value;

static CLIENT: LazyLock<Client> = LazyLock::new(|| {
    Client::builder()
        .user_agent(concat!("Steamcorder/", env!("CARGO_PKG_VERSION")))
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(120))
        .build()
        .unwrap_or_default()
});

#[derive(Debug)]
pub enum PostError {
    /// The screenshot couldn't be read.
    File(std::io::Error),
    /// Discord couldn't be reached, even after retrying.
    Unreachable,
}

/// Seconds Discord asks us to wait after a 429, capped so a worker never stalls for long.
///
/// Uses `retry_after` from the JSON body, else the `Retry-After` header, else 2 seconds.
pub fn retry_after_seconds(body: &str, header: Option<&str>) -> f64 {
    let from_body =
        serde_json::from_str::<Value>(body)
            .ok()
            .and_then(|json| match json.get("retry_after") {
                None => Some(2.0),
                Some(Value::Number(n)) => n.as_f64(),
                Some(Value::Bool(b)) => Some(f64::from(u8::from(*b))),
                Some(Value::String(s)) => s.trim().parse::<f64>().ok(),
                Some(_) => None,
            });
    let seconds = from_body
        .or_else(|| header.and_then(|h| h.trim().parse::<f64>().ok()))
        .unwrap_or(2.0);
    if seconds.is_nan() {
        2.0
    } else {
        seconds.clamp(0.5, 30.0)
    }
}

fn retry_after(response: Response) -> f64 {
    let header = response
        .headers()
        .get("Retry-After")
        .and_then(|h| h.to_str().ok())
        .map(str::to_owned);
    let body = response.text().unwrap_or_default();
    retry_after_seconds(&body, header.as_deref())
}

/// POST a file to a webhook, retrying on rate limits and brief network failures.
/// Returns the HTTP status of the final attempt.
pub fn post_file(url: &str, path: &Path) -> Result<StatusCode, PostError> {
    const ATTEMPTS: u32 = 3;
    for attempt in 1..=ATTEMPTS {
        let form = multipart::Form::new()
            .file("file", path)
            .map_err(PostError::File)?;
        let response = match CLIENT.post(url).multipart(form).send() {
            Ok(response) => response,
            Err(e) if (e.is_connect() || e.is_timeout()) && attempt < ATTEMPTS => {
                thread::sleep(Duration::from_secs(2 * u64::from(attempt)));
                continue;
            }
            Err(_) => return Err(PostError::Unreachable),
        };
        let status = response.status();
        if status == StatusCode::TOO_MANY_REQUESTS && attempt < ATTEMPTS {
            thread::sleep(Duration::from_secs_f64(retry_after(response)));
            continue;
        }
        return Ok(status);
    }
    unreachable!("the last attempt always returns")
}

/// Send a short test message. Returns whether it worked and a message for the user.
pub fn test_webhook(url: &str) -> (bool, String) {
    let body = serde_json::json!({ "content": "✅ Steamcorder is connected." });
    match CLIENT
        .post(url)
        .json(&body)
        .timeout(Duration::from_secs(10))
        .send()
    {
        Ok(r) if r.status().is_success() => (
            true,
            "Test message sent. Check your Discord channel.".into(),
        ),
        Ok(r) => (
            false,
            format!(
                "Discord rejected the webhook (HTTP {}).",
                r.status().as_u16()
            ),
        ),
        Err(_) => (
            false,
            "Couldn't reach Discord. Check your connection.".into(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::retry_after_seconds as ra;

    #[test]
    fn retry_after_from_body() {
        assert_eq!(ra(r#"{"retry_after": 1.25}"#, None), 1.25);
        assert_eq!(ra(r#"{"retry_after": "4"}"#, None), 4.0);
        assert_eq!(ra(r#"{"message": "slow down"}"#, Some("9")), 2.0);
    }

    #[test]
    fn retry_after_falls_back_to_header() {
        assert_eq!(ra("<html>", Some("7")), 7.0);
        assert_eq!(ra(r#"{"retry_after": "soon"}"#, Some("3")), 3.0);
        assert_eq!(ra("", None), 2.0);
        assert_eq!(ra("", Some("junk")), 2.0);
    }

    #[test]
    fn retry_after_is_clamped() {
        assert_eq!(ra(r#"{"retry_after": 0.01}"#, None), 0.5);
        assert_eq!(ra(r#"{"retry_after": 600}"#, None), 30.0);
        assert_eq!(ra("", Some("-3")), 0.5);
    }
}
