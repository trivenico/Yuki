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

struct ManagedProcess {
    child: Child,
    #[cfg(windows)]
    _job: windows_job::Job,
}

struct ManagedServer(Mutex<Option<ManagedProcess>>);

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

fn start_server() -> Result<ManagedProcess, Box<dyn std::error::Error>> {
    match TcpStream::connect_timeout(&server_addr(), Duration::from_millis(200)) {
        Ok(_) => return Err("Port 8765 is already occupied; refusing to attach to an unknown local process.".into()),
        Err(error) if error.kind() == std::io::ErrorKind::ConnectionRefused => {}
        Err(error) => return Err(format!("Could not verify local API port availability: {error}").into()),
    }

    let root = application_root()?;
    let python = std::env::var_os("YUKI_PYTHON").unwrap_or_else(|| {
        if cfg!(windows) { "python".into() } else { "python3".into() }
    });
    // Create the job before launching Python so its lifetime is already
    // managed if assignment succeeds immediately after process creation.
    #[cfg(windows)]
    let job = windows_job::Job::new()?;

    let mut child = Command::new(python)
        .args(["-m", "api.server", "--host", "127.0.0.1", "--port", "8765"])
        .current_dir(root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .env("PYTHONUNBUFFERED", "1")
        .spawn()?;

    #[cfg(windows)]
    if let Err(error) = job.assign(&child) {
        stop_child(&mut child);
        return Err(error.into());
    }

    Ok(ManagedProcess {
        child,
        #[cfg(windows)]
        _job: job,
    })
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

fn stop_process(process: &mut ManagedProcess) {
    stop_child(&mut process.child);
    // On Windows, dropping the job handle applies KILL_ON_JOB_CLOSE to any
    // remaining descendants. The ManagedProcess is dropped immediately after
    // this function's caller releases it.
}

fn main() {
    let result = tauri::Builder::default()
        .setup(|app| {
            let url = server_url()?;
            let mut process = start_server()?;
            if let Err(error) = wait_until_ready(&mut process.child) {
                stop_process(&mut process);
                return Err(error.into());
            }

            if let Err(error) = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Yuki")
                .inner_size(1200.0, 820.0)
                .min_inner_size(800.0, 600.0)
                .resizable(true)
                .build()
            {
                stop_process(&mut process);
                return Err(error.into());
            }

            app.manage(ManagedServer(Mutex::new(Some(process))));
            Ok(())
        })
        .build(tauri::generate_context!());

    match result {
        Ok(app) => app.run(|handle, event| {
            if matches!(event, RunEvent::Exit) {
                if let Some(server) = handle.try_state::<ManagedServer>() {
                    if let Ok(mut guard) = server.0.lock() {
                        if let Some(mut process) = guard.take() {
                            stop_process(&mut process);
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


#[cfg(windows)]
mod windows_job {
    use std::{io, mem, os::windows::io::AsRawHandle};

    use windows_sys::Win32::{
        Foundation::{CloseHandle, HANDLE},
        System::JobObjects::{
            CreateJobObjectW, SetInformationJobObject, JobObjectExtendedLimitInformation,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            AssignProcessToJobObject,
        },
    };

    pub struct Job(HANDLE);

    // A Windows kernel handle is owned by this value and may safely move
    // between threads; it is never concurrently accessed through this wrapper.
    unsafe impl Send for Job {}

    impl Job {
        pub fn new() -> io::Result<Self> {
            let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }

            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION =
                unsafe { mem::zeroed() };
            limits.BasicLimitInformation.LimitFlags =
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

            let configured = unsafe {
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as *const _,
                    mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if configured == 0 {
                let error = io::Error::last_os_error();
                unsafe { CloseHandle(handle) };
                return Err(error);
            }

            Ok(Self(handle))
        }

        pub fn assign(&self, child: &std::process::Child) -> io::Result<()> {
            let process = child.as_raw_handle() as HANDLE;
            let assigned = unsafe { AssignProcessToJobObject(self.0, process) };
            if assigned == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe { CloseHandle(self.0) };
                self.0 = std::ptr::null_mut();
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::Job;

        #[test]
        fn closing_job_terminates_an_assigned_child() {
            use std::{process::Command, thread, time::{Duration, Instant}};

            let job = Job::new().expect("Windows should create a job object");
            let mut child = Command::new("ping")
                .args(["-n", "30", "127.0.0.1"])
                .spawn()
                .expect("Windows runner should provide ping.exe");
            job.assign(&child).expect("child should be assignable to the job");

            // Allow the child to enter its wait loop before closing the job.
            thread::sleep(Duration::from_millis(250));
            drop(job);

            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                if child.try_wait().expect("child status should be readable").is_some() {
                    break;
                }
                assert!(Instant::now() < deadline, "job close did not terminate its child");
                thread::sleep(Duration::from_millis(50));
            }
            let _ = child.wait();
        }
    }
}
