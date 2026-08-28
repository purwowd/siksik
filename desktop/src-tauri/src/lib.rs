use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
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

    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let dir = std::env::temp_dir().join("satria-desktop-reports");
    std::fs::create_dir_all(&dir).map_err(|e| format!("temp dir: {e}"))?;
    let html_path = dir.join(format!("print-{stamp}.html"));
    let profile_dir = dir.join(format!("chrome-profile-{stamp}"));
    std::fs::create_dir_all(&profile_dir).map_err(|e| format!("profile dir: {e}"))?;

    let clean_html = html
        .replace("window.print()", "/* no auto print */")
        .replace("window.print ()", "/* no auto print */");
    std::fs::write(&html_path, clean_html.as_bytes()).map_err(|e| format!("tulis HTML: {e}"))?;

    let pdf_arg = format!("--print-to-pdf={}", chrome_cli_path(&pdf_path));
    let profile_arg = format!("--user-data-dir={}", chrome_cli_path(&profile_dir));
    let mut last_err = String::new();

    // Prefer loopback HTTP: Windows canonicalize() file:// URLs used to become
    // file:///?/C:/... which Chromium "repairs" until ERR_TOO_MANY_REDIRECTS,
    // then --print-to-pdf still writes that error page as a "successful" PDF.
    let printed = match serve_html_while(&clean_html, |page_url| {
        run_headless_print(&chrome, &pdf_arg, &profile_arg, page_url)
    }) {
        Ok(output) => Some(output),
        Err(err) => {
            last_err = err;
            None
        }
    };

    let ok = printed
        .as_ref()
        .is_some_and(|_| pdf_is_valid_report(&pdf_path));
    if !ok {
        let _ = std::fs::remove_file(&pdf_path);
        let fallback_profile = dir.join(format!("chrome-profile-{stamp}-file"));
        let _ = std::fs::create_dir_all(&fallback_profile);
        let fallback_profile_arg =
            format!("--user-data-dir={}", chrome_cli_path(&fallback_profile));
        let file_url = path_to_file_url(&html_path)?;
        match run_headless_print(&chrome, &pdf_arg, &fallback_profile_arg, &file_url) {
            Ok(_) => {}
            Err(err) => last_err = err,
        }
        let _ = std::fs::remove_dir_all(&fallback_profile);
    }

    let _ = std::fs::remove_file(&html_path);
    let _ = std::fs::remove_dir_all(&profile_dir);

    if !pdf_is_valid_report(&pdf_path) {
        let _ = std::fs::remove_file(&pdf_path);
        let stderr = printed
            .as_ref()
            .map(|out| String::from_utf8_lossy(&out.stderr).into_owned())
            .unwrap_or(last_err);
        return Err(format!(
            "PDF gagal dibuat (halaman error browser, bukan laporan). {}",
            stderr.chars().take(400).collect::<String>()
        ));
    }
    Ok(())
}

fn run_headless_print(
    chrome: &Path,
    pdf_arg: &str,
    profile_arg: &str,
    source_url: &str,
) -> Result<std::process::Output, String> {
    Command::new(chrome)
        .args([
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-pdf-header-footer",
            "--allow-file-access-from-files",
            "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable,HttpsFirstModeV2,TranslateUI",
            "--virtual-time-budget=15000",
            profile_arg,
            pdf_arg,
            source_url,
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("gagal menjalankan Chrome: {e}"))
}

fn serve_html_while<F, T>(html: &str, work: F) -> Result<T, String>
where
    F: FnOnce(&str) -> Result<T, String>,
{
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|e| format!("bind print server: {e}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|e| format!("print server nonblocking: {e}"))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("print server addr: {e}"))?
        .port();
    let body = html.as_bytes().to_vec();
    let stop = Arc::new(AtomicBool::new(false));
    let stop_flag = stop.clone();
    let server = thread::spawn(move || {
        let started = Instant::now();
        while !stop_flag.load(Ordering::Relaxed) && started.elapsed() < Duration::from_secs(45) {
            match listener.accept() {
                Ok((mut stream, _)) => write_print_http_response(&mut stream, &body),
                Err(err)
                    if err.kind() == std::io::ErrorKind::WouldBlock
                        || err.kind() == std::io::ErrorKind::TimedOut =>
                {
                    thread::sleep(Duration::from_millis(20));
                }
                Err(_) => break,
            }
        }
    });
    let url = format!("http://127.0.0.1:{port}/report.html");
    let result = work(&url);
    stop.store(true, Ordering::Relaxed);
    let _ = server.join();
    result
}

fn write_print_http_response(stream: &mut TcpStream, html: &[u8]) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
    let mut buf = [0u8; 4096];
    let n = stream.read(&mut buf).unwrap_or(0);
    if n == 0 || !buf[..n.min(4)].eq_ignore_ascii_case(b"GET ") {
        return;
    }
    let req = String::from_utf8_lossy(&buf[..n]);
    let first = req.lines().next().unwrap_or("");
    let wants_html = first.contains("/report.html") || first.starts_with("GET / HTTP/");
    let (status, content_type, body): (&str, &str, &[u8]) = if wants_html {
        ("200 OK", "text/html; charset=utf-8", html)
    } else {
        ("204 No Content", "text/plain", b"")
    };
    let header = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(header.as_bytes());
    if !body.is_empty() {
        let _ = stream.write_all(body);
    }
    let _ = stream.flush();
}

fn pdf_is_valid_report(pdf_path: &Path) -> bool {
    let Ok(bytes) = std::fs::read(pdf_path) else {
        return false;
    };
    if bytes.len() < 64 || !bytes.starts_with(b"%PDF") {
        return false;
    }
    const ERROR_MARKERS: [&[u8]; 5] = [
        b"ERR_TOO_MANY_REDIRECTS",
        b"ERR_FILE_NOT_FOUND",
        b"ERR_ACCESS_DENIED",
        b"ERR_INVALID_URL",
        b"This page isn't working",
    ];
    !ERROR_MARKERS.iter().any(|needle| contains_bytes(&bytes, needle))
}

fn contains_bytes(haystack: &[u8], needle: &[u8]) -> bool {
    haystack.windows(needle.len()).any(|window| window == needle)
}

fn chrome_cli_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
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
        return Ok(windows_canonical_to_file_url(&abs.to_string_lossy()));
    }
    #[cfg(not(windows))]
    {
        let encoded = abs.display().to_string().replace(' ', "%20");
        Ok(format!("file://{encoded}"))
    }
}

/// Chrome-safe file URL from a Windows canonical path.
/// `Path::canonicalize` yields `\\?\C:\...`. Replacing `\` then stripping
/// leading slashes produced `file:///?/C:/...`; Chromium then redirect-loops
/// and `--print-to-pdf` saves the ERR_TOO_MANY_REDIRECTS page.
#[cfg(any(windows, test))]
fn windows_canonical_to_file_url(canonical: &str) -> String {
    let mut s = canonical.replace('\\', "/");
    if let Some(rest) = s.strip_prefix("//?/UNC/") {
        return format!("file://{rest}");
    }
    if let Some(rest) = s.strip_prefix("//?/") {
        s = rest.to_string();
    }
    let trimmed = s.trim_start_matches('/');
    format!("file:///{trimmed}")
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

#[cfg(test)]
mod tests {
    use super::{contains_bytes, pdf_is_valid_report, windows_canonical_to_file_url};
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::path::PathBuf;

    #[test]
    fn windows_extended_path_does_not_become_query_file_url() {
        let canonical = r"\\?\C:\Users\Admin\AppData\Local\Temp\satria-desktop-reports\print-1.html";
        let legacy = {
            let s = canonical.replace('\\', "/");
            let trimmed = s.trim_start_matches('/');
            format!("file:///{trimmed}")
        };
        assert_eq!(
            legacy,
            "file:///?/C:/Users/Admin/AppData/Local/Temp/satria-desktop-reports/print-1.html"
        );
        assert_eq!(
            windows_canonical_to_file_url(canonical),
            "file:///C:/Users/Admin/AppData/Local/Temp/satria-desktop-reports/print-1.html"
        );
    }

    #[test]
    fn windows_plain_and_unc_paths_become_file_urls() {
        assert_eq!(
            windows_canonical_to_file_url(r"C:\Temp\report.html"),
            "file:///C:/Temp/report.html"
        );
        assert_eq!(
            windows_canonical_to_file_url(r"\\?\UNC\fileserver\share\a.html"),
            "file://fileserver/share/a.html"
        );
    }

    #[test]
    fn rejects_chromium_error_page_pdf() {
        let dir = std::env::temp_dir().join("satria-pdf-test");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("error.pdf");
        std::fs::write(
            &path,
            b"%PDF-1.4\n1 0 obj\n<< /Title (This page isn't working) >>\nERR_TOO_MANY_REDIRECTS\nendobj\n",
        )
        .unwrap();
        assert!(!pdf_is_valid_report(&path));
        assert!(contains_bytes(
            b"stream ERR_TOO_MANY_REDIRECTS end",
            b"ERR_TOO_MANY_REDIRECTS"
        ));
        let missing = PathBuf::from("/tmp/satria-missing-report.pdf");
        assert!(!pdf_is_valid_report(&missing));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn print_server_serves_html_and_ignores_favicon() {
        let html = "<html><body>SATRIA laporan</body></html>";
        super::serve_html_while(html, |url| {
            let mut page = TcpStream::connect(url.trim_start_matches("http://").trim_end_matches("/report.html"))
                .map_err(|e| e.to_string())?;
            page.write_all(b"GET /report.html HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                .map_err(|e| e.to_string())?;
            let mut body = String::new();
            page.read_to_string(&mut body).map_err(|e| e.to_string())?;
            assert!(body.contains("200 OK"), "{body}");
            assert!(body.contains("SATRIA laporan"), "{body}");

            let host = url.trim_start_matches("http://").trim_end_matches("/report.html");
            let mut icon = TcpStream::connect(host).map_err(|e| e.to_string())?;
            icon.write_all(b"GET /favicon.ico HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                .map_err(|e| e.to_string())?;
            let mut icon_body = String::new();
            icon.read_to_string(&mut icon_body).map_err(|e| e.to_string())?;
            assert!(icon_body.contains("204"), "{icon_body}");
            Ok(())
        })
        .unwrap();
    }
}
