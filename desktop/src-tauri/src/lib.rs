//! Tauri shell of the JacarandaParse desktop app.
//!
//! It shows one window. At start the window shows the local start-up page (splash/) while the
//! shell starts the packaged backend, `jacara-backend desktop`, with
//! - JACARA_HOME: the data folder (macOS ~/Library/Application Support/JacarandaParse, Windows
//!   %LOCALAPPDATA%\JacarandaParse: the system's local application data folder plus the program
//!   name, as Electron names `userData`);
//! - JACARA_LOG_DIR: the logs (macOS ~/Library/Logs/JacarandaParse, Windows <data folder>\logs);
//! - JACARA_WEB_DIR: the web pages shipped with the app;
//! - JACARA_WATCH_STDIN=1: the backend exits when its standard input closes, which happens when
//!   this process ends in any way, so no backend is left behind.
//!
//! The backend prints `JACARA_READY <address>` once it serves; the window then opens that address
//! (it carries the start-up token, which the backend turns into a cookie). Addresses outside the
//! backend open in the system browser, not in the window. The window does not take file drops
//! itself (`disable_drag_drop_handler`), so the pages receive HTML5 drag and drop.
//!
//! On exit the shell closes the backend's standard input and waits for it to stop its workers and
//! the model server; it is killed only when it has not stopped after STOP_WAIT.

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

const PRODUCT: &str = "JacarandaParse";
const READY: &str = "JACARA_READY ";
/// The backend stops its workers within 10 seconds and kills what is left
/// (`jacara.cli._stop`).
const STOP_WAIT: Duration = Duration::from_secs(20);

#[cfg(windows)]
const BACKEND: &str = "jacara-backend.exe";
#[cfg(not(windows))]
const BACKEND: &str = "jacara-backend";

/// The backend process, its standard input (closing it stops the backend) and the address it
/// serves.
#[derive(Default)]
struct Backend {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    origin: Mutex<Option<String>>,
    /// Address of the start-up page (tauri://localhost/ on macOS, http://tauri.localhost/ on
    /// Windows).
    start_page: Mutex<Option<Url>>,
}

fn data_folder() -> PathBuf {
    dirs::data_local_dir()
        .expect("no local application data folder")
        .join(PRODUCT)
}

fn log_folder() -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        dirs::home_dir()
            .expect("no home folder")
            .join("Library")
            .join("Logs")
            .join(PRODUCT)
    }
    #[cfg(not(target_os = "macos"))]
    {
        data_folder().join("logs")
    }
}

fn append_log(name: &str) -> std::io::Result<File> {
    let folder = log_folder();
    fs::create_dir_all(&folder)?;
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(folder.join(name))
}

fn note(line: &str) {
    if let Ok(mut log) = append_log("app.log") {
        let _ = writeln!(log, "{line}");
    }
}

/// Show the failure on the start-up page (which reads it from its query), with the logs folder to
/// attach to a report.
fn show_failure(app: &AppHandle, window: &WebviewWindow, reason: &str) {
    note(reason);
    let start = app.state::<Backend>().start_page.lock().unwrap().clone();
    if let Some(mut page) = start {
        page.query_pairs_mut()
            .clear()
            .append_pair("reason", reason)
            .append_pair("logs", &log_folder().display().to_string());
        let _ = window.navigate(page);
    }
}

fn start_backend(app: &AppHandle, window: WebviewWindow) -> Result<(), String> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource folder: {e}"))?;
    let program = resources.join("backend").join(BACKEND);
    let web = resources.join("web");
    let data = data_folder();
    fs::create_dir_all(&data).map_err(|e| format!("{}: {e}", data.display()))?;
    let stderr = append_log("backend.log").map_err(|e| format!("backend.log: {e}"))?;

    let mut command = Command::new(&program);
    command
        .arg("desktop")
        .env("JACARA_HOME", &data)
        .env("JACARA_LOG_DIR", log_folder())
        .env("JACARA_WEB_DIR", &web)
        .env("JACARA_WATCH_STDIN", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(stderr));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW: the backend and the workers it starts run without console windows.
        command.creation_flags(0x0800_0000);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("{}: {e}", program.display()))?;
    note(&format!(
        "started {} (pid {})",
        program.display(),
        child.id()
    ));

    let stdout = child.stdout.take().expect("piped");
    let state = app.state::<Backend>();
    *state.stdin.lock().unwrap() = child.stdin.take();
    *state.child.lock().unwrap() = Some(child);

    let app = app.clone();
    thread::spawn(move || {
        let mut ready = false;
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            if let Some(address) = line.strip_prefix(READY) {
                match Url::parse(address.trim()) {
                    Ok(url) => {
                        *app.state::<Backend>().origin.lock().unwrap() = Some(origin_of(&url));
                        ready = true;
                        let _ = window.navigate(url);
                    }
                    Err(e) => show_failure(&app, &window, &format!("bad ready line {line:?}: {e}")),
                }
            } else {
                note(&line);
            }
        }
        // Standard output closed: the backend has ended (or is ending on exit).
        let state = app.state::<Backend>();
        let exited = state
            .child
            .lock()
            .unwrap()
            .as_mut()
            .and_then(|c| c.wait().ok())
            .map(|s| s.to_string())
            .unwrap_or_else(|| "stopped".into());
        if state.stdin.lock().unwrap().is_some() {
            // Not asked to stop: it failed.
            let reason = if ready {
                format!("The background service stopped unexpectedly ({exited}).")
            } else {
                format!("The background service did not start ({exited}).")
            };
            show_failure(&app, &window, &reason);
        }
    });
    Ok(())
}

/// scheme://host:port of an address.
fn origin_of(url: &Url) -> String {
    url.origin().ascii_serialization()
}

/// Close the backend's standard input and wait for it to stop; kill it after STOP_WAIT.
fn stop_backend(app: &AppHandle) {
    let state = app.state::<Backend>();
    drop(state.stdin.lock().unwrap().take());
    let mut guard = state.child.lock().unwrap();
    let Some(child) = guard.as_mut() else { return };
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) if started.elapsed() < STOP_WAIT => thread::sleep(Duration::from_millis(100)),
            _ => break,
        }
    }
    note("the background service did not stop in time; killing it");
    let _ = child.kill();
    let _ = child.wait();
}

/// Navigation inside the window: the start-up page and the backend's own pages stay; any other
/// address opens in the system browser.
fn allow_navigation(app: &AppHandle, url: &Url) -> bool {
    let own_page = matches!(url.scheme(), "tauri")
        || (url.host_str() == Some("tauri.localhost") && matches!(url.scheme(), "http" | "https"));
    let backend = app.state::<Backend>().origin.lock().unwrap().clone();
    if own_page || backend.as_deref() == Some(origin_of(url).as_str()) {
        return true;
    }
    if matches!(url.scheme(), "http" | "https") {
        if let Err(e) = tauri_plugin_opener::open_url(url.as_str(), None::<&str>) {
            note(&format!("cannot open {url}: {e}"));
        }
    }
    false
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        // Only one copy runs; opening the app again brings its window forward.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .manage(Backend::default())
        .setup(|app| {
            let handle = app.handle().clone();
            let navigation = handle.clone();
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title(PRODUCT)
                    .inner_size(1280.0, 860.0)
                    .min_inner_size(960.0, 640.0)
                    .disable_drag_drop_handler()
                    .on_navigation(move |url| allow_navigation(&navigation, url))
                    .on_page_load(|_window, payload| {
                        if matches!(payload.event(), PageLoadEvent::Finished) {
                            note(&format!("loaded {}", payload.url().path()));
                        }
                    })
                    .build()?;
            *handle.state::<Backend>().start_page.lock().unwrap() = window.url().ok();
            if let Err(reason) = start_backend(&handle, window.clone()) {
                show_failure(&handle, &window, &reason);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the application");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            stop_backend(app);
        }
    });
}
