//! Strict parsing and durable cursor primitives for paired-device notifications.
//!
//! Cursor advancement is intentionally a separate operation from delivery and
//! acknowledgement. The caller must acknowledge an event before persisting its
//! sequence; otherwise a crash could skip an undelivered event on restart.

use serde::Deserialize;
use serde_json::Value;
use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};

const MAX_EVENT_ID_LEN: usize = 36;
const MAX_EVENT_TYPE_LEN: usize = 64;

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

pub fn load_cursor(path: &Path) -> io::Result<u64> {
    match fs::read_to_string(path) {
        Ok(raw) => raw.trim().parse::<u64>().map_err(|_| {
            io::Error::new(io::ErrorKind::InvalidData, "notification cursor is corrupt")
        }),
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
    fn validates_ordered_page_and_display_content() {
        let page = NotificationPage { events: vec![event(2), event(3)], next_cursor: 3, has_more: false };
        validate_page(&page, 1).expect("page should be valid");
        let display = validate_event(&page.events[0], 1).expect("event should be valid");
        assert_eq!(display.title, "Reminder");
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
