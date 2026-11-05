#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io;
use tauri::{WebviewUrl, WebviewWindowBuilder};
use url::Url;

const SERVER_URL: &str = "http://127.0.0.1:8765";

fn server_url() -> Result<Url, Box<dyn std::error::Error>> {
    let url = Url::parse(SERVER_URL)?;
    let loopback_host = matches!(
        url.host_str(),
        Some("127.0.0.1") | Some("localhost") | Some("[::1]") | Some("::1")
    );
    if url.scheme() != "http"
        || !loopback_host
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(Box::new(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Yuki Desktop only accepts its configured HTTP loopback server.",
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
