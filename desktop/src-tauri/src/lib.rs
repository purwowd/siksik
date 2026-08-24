use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, State};

const READY_PATH: &str = "/api/v1/ready";
const DEV_PORT: u16 = 8000;
const PROD_PORT: u16 = 8765;

pub struct BackendSidecar {
    child: Mutex<Option<Child>>,
    port: u16,
}

impl BackendSidecar {
    fn port(&self) -> u16 {
        self.port
    }
}

fn repo_root() -> Result<PathBuf, String> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|e| format!("repo root: {e}"))
}

fn python_executable(backend: &Path) -> PathBuf {
    let unix = backend.join(".venv/bin/python");
    if unix.is_file() {
        return unix;
    }
    #[cfg(windows)]
    {
        let win = backend.join(".venv/Scripts/python.exe");
        if win.is_file() {
            return win;
        }
    }
    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

fn probe_ready(port: u16, timeout: Duration) -> bool {
    let url = format!("http://127.0.0.1:{port}{READY_PATH}");
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    let started = Instant::now();
    while started.elapsed() < timeout {
        if let Ok(res) = client.get(&url).send() {
            if res.status().is_success() {
                return true;
            }
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn spawn_backend(port: u16, desktop_ui: bool) -> Result<Child, String> {
    let root = repo_root()?;
    let backend = root.join("backend");
    let run_py = backend.join("run.py");
    if !run_py.is_file() {
        return Err(format!("backend/run.py not found at {}", run_py.display()));
    }

    let python = python_executable(&backend);
    let mut cmd = Command::new(&python);
    cmd.args([
        "run.py",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
    ])
    .current_dir(&backend)
    .stdout(Stdio::null())
    .stderr(Stdio::null());

    if desktop_ui {
        let dist = root.join("frontend/dist");
        cmd.env("SADT_DESKTOP_UI", "1");
        cmd.env("SADT_DESKTOP_UI_DIST", dist.to_string_lossy().as_ref());
    }

    cmd.spawn()
        .map_err(|e| format!("spawn backend ({}) : {e}", python.display()))
}

/// Start backend if needed. Returns owned child when we spawned it.
fn ensure_backend(port: u16, desktop_ui: bool) -> Result<Option<Child>, String> {
    if probe_ready(port, Duration::from_secs(1)) {
        return Ok(None);
    }
    let child = spawn_backend(port, desktop_ui)?;
    if !probe_ready(port, Duration::from_secs(90)) {
        return Err(format!(
            "Backend tidak merespons di http://127.0.0.1:{port}{READY_PATH}"
        ));
    }
    Ok(Some(child))
}

#[tauri::command]
fn backend_status(state: State<'_, BackendSidecar>) -> serde_json::Value {
    let port = state.port();
    let ready = probe_ready(port, Duration::from_millis(800));
    serde_json::json!({
        "ready": ready,
        "port": port,
        "url": format!("http://127.0.0.1:{port}"),
    })
}

/// Tulis file ekspor ke path yang dipilih dialog JS (jangan blocking_save di command).
#[tauri::command]
async fn write_export_file(path: String, contents: Vec<u8>) -> Result<(), String> {
    let path_buf = PathBuf::from(path);
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(parent) = path_buf.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {e}"))?;
        }
        std::fs::write(&path_buf, &contents).map_err(|e| format!("gagal menulis file: {e}"))
    })
    .await
    .map_err(|e| format!("task: {e}"))?
}

/// Render HTML → PDF di path tujuan (Chrome headless, di background thread).
#[tauri::command]
async fn render_report_pdf(html: String, output_path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || render_html_to_pdf(&html, Path::new(&output_path)))
        .await
        .map_err(|e| format!("task: {e}"))?
}

fn render_html_to_pdf(html: &str, pdf_path: &Path) -> Result<(), String> {
    let chrome = find_chromium_binary().ok_or_else(|| {
        "Google Chrome / Chromium / Edge tidak ditemukan. Install Chrome untuk ekspor PDF di desktop."
            .to_string()
    })?;

    let pdf_path = if pdf_path.extension().and_then(|e| e.to_str()) != Some("pdf") {
        pdf_path.with_extension("pdf")
    } else {
        pdf_path.to_path_buf()
    };
    if let Some(parent) = pdf_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {e}"))?;
    }

    let dir = std::env::temp_dir().join("satria-desktop-reports");
    std::fs::create_dir_all(&dir).map_err(|e| format!("temp dir: {e}"))?;
    let html_path = dir.join(format!(
        "print-{}.html",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0)
    ));

    let clean_html = html
        .replace("window.print()", "/* no auto print */")
        .replace("window.print ()", "/* no auto print */");
    std::fs::write(&html_path, clean_html.as_bytes()).map_err(|e| format!("tulis HTML: {e}"))?;

    let file_url = path_to_file_url(&html_path)?;
    let pdf_arg = format!("--print-to-pdf={}", pdf_path.display());
    let output = Command::new(&chrome)
        .args([
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-pdf-header-footer",
            "--virtual-time-budget=10000",
            &pdf_arg,
            &file_url,
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("gagal menjalankan Chrome: {e}"))?;

    let _ = std::fs::remove_file(&html_path);

    if !pdf_path.is_file() {
        let err = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "PDF gagal dibuat (exit {}). {}",
            output.status.code().unwrap_or(-1),
            err.chars().take(400).collect::<String>()
        ));
    }
    Ok(())
}

fn find_chromium_binary() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    #[cfg(target_os = "macos")]
    {
        candidates.extend([
            PathBuf::from("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            PathBuf::from("/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
            PathBuf::from("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            PathBuf::from("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            PathBuf::from("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        ]);
    }
    #[cfg(target_os = "windows")]
    {
        let local = std::env::var_os("LOCALAPPDATA").map(PathBuf::from);
        let pf = std::env::var_os("PROGRAMFILES").map(PathBuf::from);
        let pf86 = std::env::var_os("PROGRAMFILES(X86)").map(PathBuf::from);
        for base in [local, pf, pf86].into_iter().flatten() {
            candidates.push(base.join("Google/Chrome/Application/chrome.exe"));
            candidates.push(base.join("Microsoft/Edge/Application/msedge.exe"));
            candidates.push(base.join("Chromium/Application/chrome.exe"));
        }
    }
    #[cfg(any(target_os = "linux", target_os = "freebsd"))]
    {
        candidates.extend([
            PathBuf::from("/usr/bin/google-chrome-stable"),
            PathBuf::from("/usr/bin/google-chrome"),
            PathBuf::from("/usr/bin/chromium"),
            PathBuf::from("/usr/bin/chromium-browser"),
            PathBuf::from("/snap/bin/chromium"),
            PathBuf::from("/usr/bin/microsoft-edge"),
            PathBuf::from("/usr/bin/microsoft-edge-stable"),
        ]);
    }

    for name in ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "msedge"] {
        if let Ok(path) = which_bin(name) {
            candidates.push(path);
        }
    }

    candidates.into_iter().find(|p| p.is_file())
}

fn which_bin(name: &str) -> Result<PathBuf, ()> {
    let output = Command::new("which")
        .arg(name)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .map_err(|_| ())?;
    if !output.status.success() {
        return Err(());
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if path.is_empty() {
        return Err(());
    }
    Ok(PathBuf::from(path))
}

fn path_to_file_url(path: &Path) -> Result<String, String> {
    let abs = path
        .canonicalize()
        .map_err(|e| format!("canonicalize: {e}"))?;
    #[cfg(windows)]
    {
        let s = abs.to_string_lossy().replace('\\', "/");
        let trimmed = s.trim_start_matches('/');
        return Ok(format!("file:///{trimmed}"));
    }
    #[cfg(not(windows))]
    {
        // Escape spaces for Chrome file URL
        let encoded = abs.display().to_string().replace(' ', "%20");
        Ok(format!("file://{encoded}"))
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let is_dev = cfg!(debug_assertions);
    let desktop_ui = !is_dev;
    let port = if is_dev { DEV_PORT } else { PROD_PORT };

    // Dev: jangan blok thread utama — window pakai tauri.conf devUrl (Vite :5175).
    // Backend dijamin di background supaya UI tidak "Not Responding".
    let sidecar = BackendSidecar {
        child: Mutex::new(None),
        port,
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(sidecar)
        .invoke_handler(tauri::generate_handler![
            backend_status,
            write_export_file,
            render_report_pdf
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            if is_dev {
                eprintln!(
                    "SATRIA desktop · dev UI = tauri.devUrl (Vite). Backend → :{port} (background)"
                );
                thread::spawn(move || match ensure_backend(port, false) {
                    Ok(child) => {
                        if let Some(state) = handle.try_state::<BackendSidecar>() {
                            if let Ok(mut guard) = state.child.lock() {
                                *guard = child;
                            }
                        }
                        eprintln!("SATRIA desktop · backend ready :{port}");
                    }
                    Err(err) => eprintln!("SATRIA desktop · backend start failed: {err}"),
                });
                return Ok(());
            }

            // Prod: butuh static UI dari FastAPI — pastikan sidecar sebelum navigate.
            let child = ensure_backend(port, desktop_ui).map_err(|err| {
                eprintln!("SATRIA desktop · backend start failed: {err}");
                err
            })?;
            if let Some(state) = app.try_state::<BackendSidecar>() {
                if let Ok(mut guard) = state.child.lock() {
                    *guard = child;
                }
            }

            let target = format!("http://127.0.0.1:{port}/");
            if let Some(window) = app.get_webview_window("main") {
                let parsed = target
                    .parse()
                    .map_err(|e| format!("invalid UI url {target}: {e}"))?;
                window
                    .navigate(parsed)
                    .map_err(|e| format!("navigate UI: {e}"))?;
            }
            eprintln!("SATRIA desktop · UI → {target}");
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("SATRIA desktop build")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app.try_state::<BackendSidecar>() {
                    if let Ok(mut guard) = state.child.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}
