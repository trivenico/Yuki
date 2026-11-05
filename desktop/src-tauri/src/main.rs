#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{env, io};
use tauri::{WebviewUrl, WebviewWindowBuilder};
use url::Url;

const DEFAULT_SERVER_URL: &str = "http://127.0.0.1:8765";

fn server_url() -> Result<Url, Box<dyn std::error::Error>> {
    let raw = env::var("YUKI_DESKTOP_URL")
        .unwrap_or_else(|_| DEFAULT_SERVER_URL.to_string());
    let url = Url::parse(&raw)?;

    let loopback_host = matches!(
        url.host_str(),
        Some("127.0.0.1") | Some("localhost") | Some("[::1]") | Some("::1")
    );
    if url.scheme() != "http"
        || !loopback_host
        || url.username() != ""
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(Box::new(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Yuki Desktop only accepts an HTTP loopback server URL; remote origins are disabled.",
        )));
    }
    Ok(url)
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let url = server_url()?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Yuki")
                .inner_size(1200.0, 820.0)
                .min_inner_size(800.0, 600.0)
                .resizable(true)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run Yuki Desktop");
}
