use base64::{engine::general_purpose, Engine as _};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader, Read},
    os::windows::process::CommandExt,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use windows_sys::Win32::{
    Foundation::HWND,
    UI::{
        Shell::{IsUserAnAdmin, ShellExecuteW},
        WindowsAndMessaging::SW_SHOWNORMAL,
    },
};

const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Default)]
struct AppState {
    child: Arc<Mutex<Option<Child>>>,
    hotkey: Mutex<Option<String>>,
    logs: Arc<Mutex<Vec<LogEntry>>>,
    next_log_id: Arc<Mutex<u64>>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskStatus {
    running: bool,
    code: Option<i32>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct LogEntry {
    id: u64,
    line: String,
}

#[derive(Deserialize)]
struct BridgePayload {
    command: String,
    payload: serde_json::Value,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveImagePayload {
    data_url: String,
    r#type: String,
    name: String,
    format: String,
}

#[derive(Deserialize)]
struct PathPayload {
    path: String,
}

fn mime_for_path(path: &std::path::Path) -> &'static str {
    match path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        _ => "image/png",
    }
}

fn workspace_dir(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if dir.join("tauri_bridge.exe").exists() {
                return Ok(dir.to_path_buf());
            }
            if dir.join("tauri_bridge.py").exists() {
                return Ok(dir.to_path_buf());
            }
            let bundled_resource_dir = dir.join("_up_");
            if bundled_resource_dir.join("tauri_bridge.exe").exists() {
                return Ok(bundled_resource_dir);
            }
            if bundled_resource_dir.join("tauri_bridge.py").exists() {
                return Ok(bundled_resource_dir);
            }
        }
    }
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .map(PathBuf::from)
        .or_else(|| app.path().resource_dir().ok())
        .ok_or_else(|| "无法定位项目目录".to_string())
}

fn bridge_command(cwd: &PathBuf) -> Command {
    let bridge_exe = cwd.join("tauri_bridge.exe");
    let mut cmd = if bridge_exe.exists() {
        Command::new(bridge_exe)
    } else {
        let mut python = Command::new("python");
        python.arg("-u");
        python.arg("tauri_bridge.py");
        python
    };
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

fn wide_null(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

fn is_admin() -> bool {
    unsafe { IsUserAnAdmin() != 0 }
}

fn relaunch_as_admin() -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let exe_w = wide_null(&exe.to_string_lossy());
    let verb_w = wide_null("runas");
    let result = unsafe {
        ShellExecuteW(
            0 as HWND,
            verb_w.as_ptr(),
            exe_w.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            SW_SHOWNORMAL,
        )
    };
    if (result as isize) <= 32 {
        Err("管理员权限请求被取消或失败".to_string())
    } else {
        Ok(())
    }
}

fn emit_log(app: &AppHandle, logs: &Arc<Mutex<Vec<LogEntry>>>, next_log_id: &Arc<Mutex<u64>>, line: impl Into<String>) {
    let line = line.into();
    let entry = {
        let mut next_id = match next_log_id.lock() {
            Ok(next_id) => next_id,
            Err(_) => return,
        };
        let entry = LogEntry { id: *next_id, line };
        *next_id += 1;
        entry
    };
    if let Ok(mut items) = logs.lock() {
        items.push(entry.clone());
    }
    let _ = app.emit("transport-log", entry);
}

fn finish_transport(app: &AppHandle, code: Option<i32>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
    let _ = app.emit("transport-finished", TaskStatus { running: false, code });
}

fn kill_process_tree(child: &mut Child) -> Result<(), String> {
    let pid = child.id().to_string();
    let taskkill = Command::new("taskkill")
        .args(["/PID", pid.as_str(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();

    match taskkill {
        Ok(status) if status.success() => {
            let _ = child.wait();
            Ok(())
        }
        _ => {
            child.kill().map_err(|e| e.to_string())?;
            let _ = child.wait();
            Ok(())
        }
    }
}

fn stop_running_child(app: &AppHandle, state: &AppState, reason: &str) -> Result<bool, String> {
    let child = {
        let mut guard = state.child.lock().map_err(|_| "任务状态锁定失败".to_string())?;
        guard.take()
    };

    if let Some(mut child) = child {
        kill_process_tree(&mut child)?;
        emit_log(app, &state.logs, &state.next_log_id, reason);
        finish_transport(app, Some(130));
        Ok(true)
    } else {
        Ok(false)
    }
}

fn spawn_log_reader<R>(reader: R, app: AppHandle, logs: Arc<Mutex<Vec<LogEntry>>>, next_log_id: Arc<Mutex<u64>>, prefix: &'static str)
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut bytes = Vec::new();
        loop {
            bytes.clear();
            match reader.read_until(b'\n', &mut bytes) {
                Ok(0) => break,
                Ok(_) => {
                    while matches!(bytes.last(), Some(b'\n' | b'\r')) {
                        bytes.pop();
                    }
                    if bytes.is_empty() {
                        continue;
                    }
                    let line = format!("{prefix}{}", String::from_utf8_lossy(&bytes));
                    emit_log(&app, &logs, &next_log_id, line);
                }
                Err(_) => break,
            }
        }
    });
}

fn run_bridge(app: &AppHandle, command: &str, payload: serde_json::Value) -> Result<String, String> {
    let cwd = workspace_dir(app)?;
    let json = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
    let output = bridge_command(&cwd)
        .current_dir(&cwd)
        .arg(command)
        .arg("--json")
        .arg(json)
        .output()
        .map_err(|e| format!("启动 Python 失败: {e}"))?;
    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if err.is_empty() { "Python bridge 执行失败".to_string() } else { err });
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

#[tauri::command]
fn bridge(app: AppHandle, input: BridgePayload) -> Result<serde_json::Value, String> {
    let raw = run_bridge(&app, &input.command, input.payload)?;
    if raw.starts_with('{') || raw.starts_with('[') {
        serde_json::from_str(&raw).map_err(|e| e.to_string())
    } else {
        Ok(serde_json::json!(raw))
    }
}

#[tauri::command]
fn start_transport(app: AppHandle, state: State<AppState>, payload: serde_json::Value) -> Result<(), String> {
    let mut guard = state.child.lock().map_err(|_| "任务状态锁定失败".to_string())?;
    if guard.is_some() {
        return Err("当前已有搬运任务在执行".to_string());
    }

    let cwd = workspace_dir(&app)?;
    let command = if payload.get("tasks").is_some() { "batch" } else { "run" };
    let json = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
    if let Ok(mut logs) = state.logs.lock() {
        logs.clear();
    }
    if let Ok(mut next_id) = state.next_log_id.lock() {
        *next_id = 1;
    }

    let mut child = bridge_command(&cwd)
        .current_dir(&cwd)
        .arg(command)
        .arg("--json")
        .arg(json)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("启动搬运任务失败: {e}"))?;

    if let Some(stdout) = child.stdout.take() {
        spawn_log_reader(stdout, app.clone(), state.inner().logs.clone(), state.inner().next_log_id.clone(), "");
    }
    if let Some(stderr) = child.stderr.take() {
        spawn_log_reader(stderr, app.clone(), state.inner().logs.clone(), state.inner().next_log_id.clone(), "ERROR: ");
    }

    *guard = Some(child);
    drop(guard);

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }

    let app_for_wait = app.clone();
    let state_for_wait = state.inner().child.clone();
    thread::spawn(move || {
        loop {
            let maybe_code = {
                let mut guard = match state_for_wait.lock() {
                    Ok(guard) => guard,
                    Err(_) => break,
                };
                match guard.as_mut() {
                    Some(child) => match child.try_wait() {
                        Ok(Some(status)) => {
                            *guard = None;
                            Some(status.code())
                        }
                        Ok(None) => None,
                        Err(_) => {
                            *guard = None;
                            Some(None)
                        }
                    },
                    None => break,
                }
            };
            if let Some(code) = maybe_code {
                finish_transport(&app_for_wait, code);
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(250));
        }
    });

    Ok(())
}

#[tauri::command]
fn stop_transport(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    stop_running_child(&app, state.inner(), "已发送停止请求。")?;
    Ok(())
}

#[tauri::command]
fn transport_status(state: State<AppState>) -> Result<TaskStatus, String> {
    let guard = state
        .child
        .lock()
        .map_err(|_| "任务状态锁定失败".to_string())?;
    Ok(TaskStatus {
        running: guard.is_some(),
        code: None,
    })
}

#[tauri::command]
fn transport_logs(state: State<AppState>) -> Result<Vec<LogEntry>, String> {
    state
        .logs
        .lock()
        .map(|logs| logs.clone())
        .map_err(|_| "日志状态锁定失败".to_string())
}

#[tauri::command]
fn save_image_direct(app: AppHandle, payload: SaveImagePayload) -> Result<(), String> {
    let cwd = workspace_dir(&app)?;
    let target_type = payload.r#type.trim();
    if target_type != "item" && target_type != "region" {
        return Err("资源类型必须是 item 或 region".to_string());
    }

    let name = payload.name.trim();
    if name.is_empty()
        || name.contains('/')
        || name.contains('\\')
        || name.contains(':')
        || name.contains("..")
    {
        return Err("文件名无效".to_string());
    }

    let format = payload.format.trim().to_ascii_lowercase();
    let suffix = match format.as_str() {
        "png" => "png",
        "jpg" | "jpeg" => "jpg",
        "webp" => "webp",
        _ => return Err("不支持的图片格式".to_string()),
    };

    let (_, encoded) = payload
        .data_url
        .split_once(',')
        .ok_or_else(|| "图片数据格式无效".to_string())?;
    let bytes = general_purpose::STANDARD
        .decode(encoded)
        .map_err(|e| format!("图片数据解码失败: {e}"))?;

    let target_dir = cwd.join(target_type);
    fs::create_dir_all(&target_dir).map_err(|e| e.to_string())?;
    let output_path = target_dir.join(format!("{name}.{suffix}"));
    let temp_path = target_dir.join(format!("_{name}.{suffix}"));
    fs::write(&temp_path, bytes).map_err(|e| e.to_string())?;
    if output_path.exists() {
        fs::remove_file(&output_path).map_err(|e| e.to_string())?;
    }
    fs::rename(&temp_path, &output_path).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn image_data_direct(payload: PathPayload) -> Result<String, String> {
    let path = PathBuf::from(payload.path);
    let bytes = fs::read(&path).map_err(|e| e.to_string())?;
    Ok(format!(
        "data:{};base64,{}",
        mime_for_path(&path),
        general_purpose::STANDARD.encode(bytes)
    ))
}

#[tauri::command]
fn delete_image_direct(app: AppHandle, payload: PathPayload) -> Result<(), String> {
    let cwd = workspace_dir(&app)?;
    let path = PathBuf::from(payload.path);
    let resolved = path.canonicalize().map_err(|e| e.to_string())?;
    let item_dir = cwd.join("item").canonicalize().map_err(|e| e.to_string())?;
    let region_dir = cwd.join("region").canonicalize().map_err(|e| e.to_string())?;
    if !resolved.starts_with(item_dir) && !resolved.starts_with(region_dir) {
        return Err("只能删除 item 或 region 目录内的图片".to_string());
    }
    fs::remove_file(resolved).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_temp_image_direct(app: AppHandle, data_url: String) -> Result<String, String> {
    let cwd = workspace_dir(&app)?;
    let (_, encoded) = data_url
        .split_once(',')
        .ok_or_else(|| "图片数据格式无效".to_string())?;
    let bytes = general_purpose::STANDARD
        .decode(encoded)
        .map_err(|e| format!("图片数据解码失败: {e}"))?;
    let temp_dir = cwd.join("temp").join("item");
    fs::create_dir_all(&temp_dir).map_err(|e| e.to_string())?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_millis();
    let output_path = temp_dir.join(format!("liquid_output_{now}.png"));
    fs::write(&output_path, bytes).map_err(|e| e.to_string())?;
    Ok(output_path.to_string_lossy().to_string())
}

#[tauri::command]
fn register_hotkey(app: AppHandle, state: State<AppState>, shortcut: String) -> Result<(), String> {
    let normalized = shortcut.replace("Ctrl", "CommandOrControl");
    app.global_shortcut().unregister_all().map_err(|e| e.to_string())?;
    app.global_shortcut()
        .on_shortcut(normalized.as_str(), |app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                let state = app.state::<AppState>();
                let _ = stop_running_child(app, state.inner(), "已通过停止快捷键请求停止任务。");
            }
        })
        .map_err(|e| e.to_string())?;
    *state.hotkey.lock().map_err(|_| "快捷键状态锁定失败".to_string())? = Some(normalized);
    Ok(())
}

#[tauri::command]
fn unregister_hotkey(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    app.global_shortcut().unregister_all().map_err(|e| e.to_string())?;
    *state.hotkey.lock().map_err(|_| "快捷键状态锁定失败".to_string())? = None;
    Ok(())
}

pub fn run() {
    if !is_admin() {
        if relaunch_as_admin().is_ok() {
            return;
        }
    }

    tauri::Builder::default()
        .manage(AppState::default())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .build(),
        )
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let child = {
                    let state = window.state::<AppState>();
                    let child = match state.child.lock() {
                        Ok(mut guard) => guard.take(),
                        Err(_) => None,
                    };
                    child
                };
                if let Some(mut child) = child {
                    let _ = kill_process_tree(&mut child);
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            bridge,
            start_transport,
            stop_transport,
            transport_status,
            transport_logs,
            save_image_direct,
            image_data_direct,
            delete_image_direct,
            save_temp_image_direct,
            register_hotkey,
            unregister_hotkey
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
