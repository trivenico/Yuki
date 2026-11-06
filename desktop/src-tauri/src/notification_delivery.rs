//! Strict parsing and durable cursor primitives for paired-device notifications.
//!
//! Cursor advancement is intentionally a separate operation from delivery and
//! acknowledgement. The caller must acknowledge an event before persisting its
//! sequence; otherwise a crash could skip an undelivered event on restart.

use serde::Deserialize;
use serde_json::Value;
use url::Url;
use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};

const MAX_EVENT_ID_LEN: usize = 36;
const MAX_EVENT_TYPE_LEN: usize = 64;
// Keep the native parser aligned with the outbox's hard page-size ceiling.
const MAX_NOTIFICATION_PAGE_EVENTS: usize = 100;
const MAX_NOTIFICATION_CURSOR: u64 = i64::MAX as u64;

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct NotificationEvent {
    pub event_id: String,
    pub sequence: u64,
    pub event_type: String,
    pub payload: Value,
    pub created_at: String,
    pub expires_at: String,
    pub acknowledged_at: Option<String>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct NotificationPage {
    pub events: Vec<NotificationEvent>,
    pub next_cursor: u64,
    pub has_more: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DisplayNotification {
    pub title: String,
    pub body: String,
}

fn safe_text(value: &str, max_chars: usize) -> bool {
    !value.trim().is_empty()
        && value.chars().count() <= max_chars
        && !value.chars().any(char::is_control)
}

pub fn validate_event(event: &NotificationEvent, after_sequence: u64) -> io::Result<DisplayNotification> {
    let valid_id = event.event_id.len() == MAX_EVENT_ID_LEN
        && event.event_id.starts_with("evt_")
        && event.event_id[4..].bytes().all(|byte| byte.is_ascii_hexdigit());
    let valid_type = !event.event_type.is_empty()
        && event.event_type.len() <= MAX_EVENT_TYPE_LEN
        && event.event_type.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"_.-".contains(&byte));
    if !valid_id || !valid_type || event.sequence <= after_sequence {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid notification event metadata"));
    }

    let title = event.payload.get("title").and_then(Value::as_str)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "notification title missing"))?;
    let body = event.payload.get("body").and_then(Value::as_str)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "notification body missing"))?;
    if !safe_text(title, 120) || !safe_text(body, 1_000) {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid notification text"));
    }
    Ok(DisplayNotification { title: title.to_owned(), body: body.to_owned() })
}

pub fn validate_page(page: &NotificationPage, after_sequence: u64) -> io::Result<()> {
    if page.events.len() > MAX_NOTIFICATION_PAGE_EVENTS
        || after_sequence > MAX_NOTIFICATION_CURSOR
        || page.next_cursor > MAX_NOTIFICATION_CURSOR
    {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "notification page exceeds supported bounds"));
    }
    let mut previous = after_sequence;
    for event in &page.events {
        if event.sequence <= previous || event.sequence > page.next_cursor {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "notification page sequence is not monotonic"));
        }
        previous = event.sequence;
        validate_event(event, after_sequence)?;
    }
    if page.next_cursor < after_sequence
        || (!page.events.is_empty() && page.next_cursor != previous)
        || (page.events.is_empty() && page.next_cursor != after_sequence)
    {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "notification page cursor is inconsistent"));
    }
    Ok(())
}

/// Build a feed URL only for Yuki's fixed local API origin.
pub fn notification_feed_url(base: &str, cursor: u64) -> io::Result<Url> {
    let mut url = Url::parse(base).map_err(|_| {
        io::Error::new(io::ErrorKind::InvalidInput, "notification API URL is invalid")
    })?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port_or_known_default() != Some(8765)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "notification API must use the fixed loopback origin",
        ));
    }
    if cursor > MAX_NOTIFICATION_CURSOR {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "notification cursor exceeds supported range"));
    }
    url.set_path("/v1/devices/notifications");
    url.query_pairs_mut().append_pair("after_sequence", &cursor.to_string());
    url.query_pairs_mut().append_pair("limit", "50");
    Ok(url)
}

/// Build an acknowledgement URL after validating the server-issued event ID.
pub fn notification_ack_url(base: &str, event_id: &str) -> io::Result<Url> {
    let mut url = Url::parse(base).map_err(|_| {
        io::Error::new(io::ErrorKind::InvalidInput, "notification API URL is invalid")
    })?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port_or_known_default() != Some(8765)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(io::Error::new(io::ErrorKind::PermissionDenied, "notification API must use the fixed loopback origin"));
    }
    let valid_id = event_id.len() == MAX_EVENT_ID_LEN
        && event_id.starts_with("evt_")
        && event_id[4..].bytes().all(|byte| byte.is_ascii_hexdigit());
    if !valid_id {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "notification event ID is invalid"));
    }
    url.set_path(&format!("/v1/devices/notifications/{event_id}/ack"));
    Ok(url)
}

/// Accept only the exact success envelope returned by the device acknowledgement API.
/// The worker must call this before persisting the corresponding cursor.
pub fn validate_acknowledgement(response: &[u8]) -> io::Result<()> {
    let value: Value = serde_json::from_slice(response).map_err(|_| {
        io::Error::new(io::ErrorKind::InvalidData, "notification acknowledgement is malformed")
    })?;
    let object = value.as_object().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidData, "notification acknowledgement is not an object")
    })?;
    if object.len() != 1 || object.get("acknowledged").and_then(Value::as_bool) != Some(true) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "notification acknowledgement was not confirmed",
        ));
    }
    Ok(())
}

pub fn load_cursor(path: &Path) -> io::Result<u64> {
    match fs::read_to_string(path) {
        Ok(raw) => {
            let cursor = raw.trim().parse::<u64>().map_err(|_| {
                io::Error::new(io::ErrorKind::InvalidData, "notification cursor is corrupt")
            })?;
            if cursor > MAX_NOTIFICATION_CURSOR {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "notification cursor exceeds supported range",
                ));
            }
            Ok(cursor)
        },
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(0),
        Err(error) => Err(error),
    }
}

#[cfg(not(windows))]
fn replace_cursor_file(source: &Path, destination: &Path) -> io::Result<()> {
    fs::rename(source, destination)
}

#[cfg(windows)]
fn replace_cursor_file(source: &Path, destination: &Path) -> io::Result<()> {
    use std::{os::windows::ffi::OsStrExt};
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination.as_os_str().encode_wide().chain(Some(0)).collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

pub fn persist_cursor(path: &Path, cursor: u64) -> io::Result<()> {
    if cursor > MAX_NOTIFICATION_CURSOR {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "notification cursor exceeds supported range"));
    }
    let current = load_cursor(path)?;
    if cursor < current {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "notification cursor cannot move backwards"));
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut temporary = PathBuf::from(path);
    let suffix = format!("{}.tmp", std::process::id());
    let name = path.file_name().ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "cursor path has no filename"))?;
    let mut temporary_name = name.to_os_string();
    temporary_name.push(suffix);
    temporary.set_file_name(temporary_name);

    let result = (|| {
        let mut file = OpenOptions::new().write(true).create_new(true).open(&temporary)?;
        writeln!(file, "{cursor}")?;
        file.sync_all()?;
        replace_cursor_file(&temporary, path)?;
        if let Some(parent) = path.parent() {
            if let Ok(directory) = OpenOptions::new().read(true).open(parent) {
                let _ = directory.sync_all();
            }
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn event(sequence: u64) -> NotificationEvent {
        NotificationEvent {
            event_id: format!("evt_{:032x}", sequence),
            sequence,
            event_type: "reminder.due".to_string(),
            payload: serde_json::json!({"title": "Reminder", "body": "A task is due"}),
            created_at: "2026-01-01T00:00:00+00:00".to_string(),
            expires_at: "2027-01-01T00:00:00+00:00".to_string(),
            acknowledged_at: None,
        }
    }

    #[test]
    fn notification_urls_are_pinned_to_the_local_api() {
        let feed = notification_feed_url("http://127.0.0.1:8765/", 12).unwrap();
        assert_eq!(feed.as_str(), "http://127.0.0.1:8765/v1/devices/notifications?after_sequence=12&limit=50");
        let ack = notification_ack_url("http://127.0.0.1:8765/", &format!("evt_{:032x}", 7)).unwrap();
        assert!(ack.path().ends_with("/evt_00000000000000000000000000000007/ack"));
        assert!(notification_feed_url("https://example.com/", 0).is_err());
        assert!(notification_feed_url("http://127.0.0.1:8765/override", 0).is_err());
        assert!(notification_feed_url("http://user@127.0.0.1:8765/", 0).is_err());
        assert!(notification_ack_url("http://127.0.0.1:8765/", "evt_bad").is_err());
        assert!(notification_feed_url("http://127.0.0.1:8765/", MAX_NOTIFICATION_CURSOR + 1).is_err());
    }

    #[test]
    fn accepts_only_explicit_acknowledgement_success() {
        assert!(validate_acknowledgement(br#"{"acknowledged":true}"#).is_ok());
        assert_eq!(validate_acknowledgement(br#"{"acknowledged":false}"#).unwrap_err().kind(), io::ErrorKind::InvalidData);
        assert!(validate_acknowledgement(br#"{"acknowledged":true,"extra":1}"#).is_err());
        assert!(validate_acknowledgement(br#"{"acknowledged":"true"}"#).is_err());
        assert!(validate_acknowledgement(b"not-json").is_err());
    }

    #[test]
    fn validates_ordered_page_and_display_content() {
        let page = NotificationPage { events: vec![event(2), event(3)], next_cursor: 3, has_more: false };
        validate_page(&page, 1).expect("page should be valid");
        let display = validate_event(&page.events[0], 1).expect("event should be valid");
        assert_eq!(display.title, "Reminder");
    }

    #[test]
    fn rejects_pages_larger_than_the_server_contract() {
        let events = (1..=MAX_NOTIFICATION_PAGE_EVENTS + 1)
            .map(|sequence| event(sequence as u64))
            .collect();
        let page = NotificationPage {
            events,
            next_cursor: (MAX_NOTIFICATION_PAGE_EVENTS + 1) as u64,
            has_more: true,
        };
        assert_eq!(validate_page(&page, 0).unwrap_err().kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn rejects_cursors_outside_the_sqlite_sequence_range() {
        let page = NotificationPage { events: Vec::new(), next_cursor: 0, has_more: false };
        assert!(validate_page(&page, MAX_NOTIFICATION_CURSOR + 1).is_err());
        let page = NotificationPage { events: Vec::new(), next_cursor: MAX_NOTIFICATION_CURSOR + 1, has_more: false };
        assert!(validate_page(&page, 0).is_err());
    }

    #[test]
    fn rejects_out_of_order_or_inconsistent_page() {
        let page = NotificationPage { events: vec![event(3), event(2)], next_cursor: 3, has_more: false };
        assert!(validate_page(&page, 1).is_err());
    }

    #[test]
    fn rejects_untrusted_notification_text() {
        let mut item = event(1);
        item.payload = serde_json::json!({"title": "bad\ntext", "body": "body"});
        assert!(validate_event(&item, 0).is_err());
        item.payload = serde_json::json!({"title": "Reminder", "body": " "});
        assert!(validate_event(&item, 0).is_err());
    }

    #[test]
    fn cursor_rejects_values_outside_supported_range() {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("yuki-cursor-range-{}-{nonce}", std::process::id()));
        assert_eq!(
            persist_cursor(&path, MAX_NOTIFICATION_CURSOR + 1).unwrap_err().kind(),
            io::ErrorKind::InvalidInput
        );
        assert!(!path.exists());
    }

    #[test]
    fn load_cursor_rejects_values_outside_supported_range() {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("yuki-cursor-corrupt-{}-{nonce}", std::process::id()));
        fs::write(&path, format!("{}\n", MAX_NOTIFICATION_CURSOR + 1)).unwrap();
        assert_eq!(load_cursor(&path).unwrap_err().kind(), io::ErrorKind::InvalidData);
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn cursor_cannot_move_backwards() {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let directory = std::env::temp_dir().join(format!("yuki-cursor-monotonic-{}-{nonce}", std::process::id()));
        let path = directory.join("notification.cursor");
        persist_cursor(&path, 42).unwrap();
        assert_eq!(
            persist_cursor(&path, 41).unwrap_err().kind(),
            io::ErrorKind::InvalidInput
        );
        assert_eq!(load_cursor(&path).unwrap(), 42);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn cursor_round_trips_and_missing_cursor_starts_at_zero() {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let directory = std::env::temp_dir().join(format!("yuki-cursor-{}-{nonce}", std::process::id()));
        let path = directory.join("notification.cursor");
        assert_eq!(load_cursor(&path).unwrap(), 0);
        persist_cursor(&path, 42).unwrap();
        assert_eq!(load_cursor(&path).unwrap(), 42);
        // Replacing an existing cursor must work on Windows as well as Unix.
        persist_cursor(&path, 43).unwrap();
        assert_eq!(load_cursor(&path).unwrap(), 43);
        fs::remove_dir_all(directory).unwrap();
    }
}
