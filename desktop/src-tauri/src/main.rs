#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use url::Url;

const SERVER_URL: &str = "http://127.0.0.1:8765";
fn server_addr() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], 8765))
}
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_HEALTH_RESPONSE: u64 = 4096;

struct ManagedServer(Mutex<Option<Child>>);

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
        return Err("Yuki Desktop only accepts its configured HTTP loopback server.".into());
    }
    Ok(url)
}

fn application_root() -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Some(path) = std::env::var_os("YUKI_HOME") {
        let root = PathBuf::from(path);
        if root.join("api").join("server.py").is_file() {
            return Ok(root);
        }
        return Err("YUKI_HOME must contain api/server.py.".into());
    }

    if cfg!(debug_assertions) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        if root.join("api").join("server.py").is_file() {
            return Ok(root);
        }
    }
    Err("Set YUKI_HOME to the installed Yuki application directory.".into())
}

fn start_server() -> Result<Child, Box<dyn std::error::Error>> {
    match TcpStream::connect_timeout(&server_addr(), Duration::from_millis(200)) {
        Ok(_) => return Err("Port 8765 is already occupied; refusing to attach to an unknown local process.".into()),
        Err(error) if error.kind() == std::io::ErrorKind::ConnectionRefused => {}
        Err(error) => return Err(format!("Could not verify local API port availability: {error}").into()),
    }

    let root = application_root()?;
    let python = std::env::var_os("YUKI_PYTHON").unwrap_or_else(|| {
        if cfg!(windows) { "python".into() } else { "python3".into() }
    });
    let child = Command::new(python)
        .args(["-m", "api.server", "--host", "127.0.0.1", "--port", "8765"])
        .current_dir(root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .env("PYTHONUNBUFFERED", "1")
        .spawn()?;
    Ok(child)
}

fn health_response_is_ready(response: &[u8]) -> bool {
    let response = String::from_utf8_lossy(response);
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    if headers.lines().next() != Some("HTTP/1.1 200 OK") {
        return false;
    }
    let Ok(payload) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    payload.get("status").and_then(serde_json::Value::as_str) == Some("ok")
}

fn health_check() -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&server_addr(), Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let request = b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }

    let mut response = Vec::with_capacity(512);
    if stream
        .take(MAX_HEALTH_RESPONSE)
        .read_to_end(&mut response)
        .is_err()
    {
        return false;
    }
    health_response_is_ready(&response)
}

fn wait_until_ready(child: &mut Child) -> Result<(), Box<dyn std::error::Error>> {
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if let Some(status) = child.try_wait()? {
            return Err(format!("Yuki API process exited during startup ({status}).").into());
        }
        if health_check() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
    let _ = child.wait();
    Err("Yuki API did not become ready within 30 seconds.".into())
}

fn stop_child(child: &mut Child) {
    if child.try_wait().ok().flatten().is_none() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn main() {
    let result = tauri::Builder::default()
        .setup(|app| {
            let url = server_url()?;
            let mut child = start_server()?;
            if let Err(error) = wait_until_ready(&mut child) {
                stop_child(&mut child);
                return Err(error.into());
            }

            if let Err(error) = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Yuki")
                .inner_size(1200.0, 820.0)
                .min_inner_size(800.0, 600.0)
                .resizable(true)
                .build()
            {
                stop_child(&mut child);
                return Err(error.into());
            }

            app.manage(ManagedServer(Mutex::new(Some(child))));
            Ok(())
        })
        .build(tauri::generate_context!());

    match result {
        Ok(app) => app.run(|handle, event| {
            if matches!(event, RunEvent::Exit) {
                if let Some(server) = handle.try_state::<ManagedServer>() {
                    if let Ok(mut guard) = server.0.lock() {
                        if let Some(mut child) = guard.take() {
                            stop_child(&mut child);
                        }
                    }
                }
            }
        }),
        Err(error) => {
            eprintln!("Yuki Desktop startup failed: {error}");
            std::process::exit(1);
        }
    }
}


#[cfg(test)]
mod tests {
    use super::health_response_is_ready;

    #[test]
    fn accepts_only_successful_health_response() {
        let response = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\"}";
        assert!(health_response_is_ready(response));
    }

    #[test]
    fn rejects_non_success_http_status() {
        let response = b"HTTP/1.1 503 Service Unavailable\r\n\r\n{\"status\":\"ok\"}";
        assert!(!health_response_is_ready(response));
    }

    #[test]
    fn rejects_malformed_or_unhealthy_json() {
        assert!(!health_response_is_ready(b"HTTP/1.1 200 OK\r\n\r\n{"));
        assert!(!health_response_is_ready(b"HTTP/1.1 200 OK\r\n\r\n{\"status\":\"starting\"}"));
        assert!(!health_response_is_ready(b"HTTP/1.1 200 OK\r\n\r\n{\"message\":\"ok\"}"));
    }
}
