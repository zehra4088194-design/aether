use serde::Serialize;
use std::net::TcpListener;
use std::sync::Mutex;
#[cfg(target_os = "windows")]
use std::{os::windows::process::CommandExt, process::Command};
use tauri::{Manager, State};
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

#[derive(Default)]
struct BackendProcess(Mutex<Option<tauri_plugin_shell::process::CommandChild>>);

#[derive(Clone)]
struct BackendCredentials {
    token: String,
    port: u16,
}

#[derive(Serialize)]
struct BackendInfo {
    base_url: String,
    token: String,
}

#[tauri::command]
fn backend_info(credentials: State<'_, BackendCredentials>) -> BackendInfo {
    BackendInfo {
        base_url: format!("http://127.0.0.1:{}", credentials.port),
        token: credentials.token.clone(),
    }
}

fn license_environment() -> Vec<(&'static str, String)> {
    let license_url = std::env::var("AETHER_LICENSE_URL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| option_env!("AETHER_LICENSE_URL").map(|value| value.to_string()))
        .unwrap_or_default();
    let license_required = std::env::var("AETHER_LICENSE_REQUIRED")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| option_env!("AETHER_LICENSE_REQUIRED").map(|value| value.to_string()))
        .unwrap_or_else(|| "0".to_string());
    vec![
        ("AETHER_LICENSE_URL", license_url),
        ("AETHER_LICENSE_REQUIRED", license_required),
    ]
}

fn available_loopback_port() -> u16 {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .unwrap_or(8765)
}

fn start_backend(app: &tauri::AppHandle, token: &str, port: u16) -> Option<tauri_plugin_shell::process::CommandChild> {
    let mut envs = vec![
        ("AETHER_API_TOKEN", token.to_string()),
        ("AETHER_API_PORT", port.to_string()),
        ("AETHER_PARENT_PID", std::process::id().to_string()),
    ];
    envs.extend(license_environment());
    if let Ok(resource_dir) = app.path().resource_dir() {
        envs.push((
            "PLAYWRIGHT_BROWSERS_PATH",
            resource_dir.join("ms-playwright").to_string_lossy().into_owned(),
        ));
        envs.push((
            "AETHER_PIPER_VOICE_DIR",
            resource_dir.join("piper-voices").to_string_lossy().into_owned(),
        ));
    }

    #[cfg(debug_assertions)]
    let command = app
        .shell()
        .command("python")
        .args(["-m", "aether.api"])
        .envs(envs);

    #[cfg(not(debug_assertions))]
    let command = app
        .shell()
        .sidecar("aether-backend")
        .expect("Aether backend sidecar is missing")
        .envs(envs);

    match command.spawn() {
        Ok((_events, child)) => Some(child),
        Err(error) => {
            eprintln!("Could not start Aether backend: {error}");
            None
        }
    }
}

fn terminate_backend(child: tauri_plugin_shell::process::CommandChild) {
    #[cfg(target_os = "windows")]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let pid = child.pid().to_string();
        let terminated = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
        if !terminated {
            let _ = child.kill();
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        let _ = child.kill();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let token = Uuid::new_v4().to_string();
    // A random free loopback port allows a dev backend and a packaged app to
    // coexist, while the per-launch token prevents another local process from
    // using Aether's privileged API.
    let port = available_loopback_port();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(BackendCredentials { token: token.clone(), port })
        .manage(BackendProcess::default())
        .invoke_handler(tauri::generate_handler![backend_info])
        .setup(move |app| {
            let child = start_backend(app.handle(), &token, port);
            *app.state::<BackendProcess>().0.lock().expect("backend mutex poisoned") = child;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                let state = window.state::<BackendProcess>();
                let child = state.0.lock().expect("backend mutex poisoned").take();
                if let Some(child) = child {
                    terminate_backend(child);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Aether");
}
