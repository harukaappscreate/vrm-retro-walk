use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{
    ipc::Response,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    DragDropEvent, LogicalPosition, LogicalSize, Manager, Position, RunEvent, Size, State, Window,
    WindowEvent,
};

const FRAME_W: usize = 144;
const FRAME_H: usize = 164;
const ASPECT_RATIO: f64 = 144.0 / 164.0;
const BASE_W: f64 = 246.0;
const BASE_H: f64 = 280.0;

#[derive(Clone, Debug)]
struct PerchWindow {
    id: u64,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
struct SavedSettings {
    last_vrm_path: Option<String>,
    win_h: f64,
    style: String,
    outline: bool,
    sfx_on: bool,
    pinned: bool,
    perch_enabled: bool,
    intro_enabled: bool,
    click_through: bool,
}

impl Default for SavedSettings {
    fn default() -> Self {
        Self {
            last_vrm_path: None,
            win_h: BASE_H,
            style: "CRISP".to_string(),
            outline: true,
            sfx_on: true,
            pinned: true,
            perch_enabled: true,
            intro_enabled: true,
            click_through: true,
        }
    }
}

struct SettingsState {
    path: PathBuf,
    config_dir: PathBuf,
    data: Mutex<SavedSettings>,
}

impl SettingsState {
    fn persist(&self, snapshot: &SavedSettings) {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(json) = serde_json::to_string_pretty(snapshot) {
            let _ = std::fs::write(&self.path, json);
        }
    }
}

#[cfg(target_os = "macos")]
mod os_windows {
    use super::PerchWindow;
    use std::ffi::c_void;

    #[repr(C)]
    #[derive(Default, Copy, Clone)]
    struct CGPoint {
        x: f64,
        y: f64,
    }
    #[repr(C)]
    #[derive(Default, Copy, Clone)]
    struct CGSize {
        width: f64,
        height: f64,
    }
    #[repr(C)]
    #[derive(Default, Copy, Clone)]
    struct CGRect {
        origin: CGPoint,
        size: CGSize,
    }

    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        fn CGWindowListCopyWindowInfo(option: u32, relative_to_window: u32) -> *const c_void;
        fn CGRectMakeWithDictionaryRepresentation(dict: *const c_void, rect: *mut CGRect) -> bool;
        fn CGEventCreate(source: *const c_void) -> *const c_void;
        fn CGEventGetLocation(event: *const c_void) -> CGPoint;
        static kCGWindowLayer: *const c_void;
        static kCGWindowOwnerPID: *const c_void;
        static kCGWindowNumber: *const c_void;
        static kCGWindowBounds: *const c_void;
        static kCGWindowAlpha: *const c_void;
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFArrayGetCount(arr: *const c_void) -> isize;
        fn CFArrayGetValueAtIndex(arr: *const c_void, idx: isize) -> *const c_void;
        fn CFDictionaryGetValue(dict: *const c_void, key: *const c_void) -> *const c_void;
        fn CFNumberGetValue(num: *const c_void, kind: isize, out: *mut c_void) -> bool;
        fn CFRelease(obj: *const c_void);
    }

    pub fn global_cursor_logical() -> Option<(f64, f64)> {
        unsafe {
            let ev = CGEventCreate(std::ptr::null());
            if ev.is_null() {
                return None;
            }
            let pt = CGEventGetLocation(ev);
            CFRelease(ev);
            Some((pt.x, pt.y))
        }
    }

    pub fn list_visible_windows() -> Vec<PerchWindow> {
        const OPTION_ON_SCREEN_ONLY: u32 = 1;
        const EXCLUDE_DESKTOP: u32 = 16;
        const CF_SINT32: isize = 3;
        const CF_FLOAT64: isize = 13;

        let my_pid = std::process::id() as i32;
        let mut out = Vec::new();
        unsafe {
            let arr = CGWindowListCopyWindowInfo(OPTION_ON_SCREEN_ONLY | EXCLUDE_DESKTOP, 0);
            if arr.is_null() {
                return out;
            }
            let n = CFArrayGetCount(arr);
            for i in 0..n {
                let d = CFArrayGetValueAtIndex(arr, i);
                if d.is_null() {
                    continue;
                }
                let mut layer: i32 = -1;
                let mut pid: i32 = 0;
                let mut wid: i32 = 0;
                let mut alpha: f64 = 1.0;
                let p_l = CFDictionaryGetValue(d, kCGWindowLayer);
                let p_p = CFDictionaryGetValue(d, kCGWindowOwnerPID);
                let p_w = CFDictionaryGetValue(d, kCGWindowNumber);
                let p_a = CFDictionaryGetValue(d, kCGWindowAlpha);
                let p_b = CFDictionaryGetValue(d, kCGWindowBounds);
                if !p_l.is_null() {
                    CFNumberGetValue(p_l, CF_SINT32, (&mut layer as *mut i32).cast());
                }
                if !p_p.is_null() {
                    CFNumberGetValue(p_p, CF_SINT32, (&mut pid as *mut i32).cast());
                }
                if !p_w.is_null() {
                    CFNumberGetValue(p_w, CF_SINT32, (&mut wid as *mut i32).cast());
                }
                if !p_a.is_null() {
                    CFNumberGetValue(p_a, CF_FLOAT64, (&mut alpha as *mut f64).cast());
                }
                if layer != 0 || pid == my_pid || alpha <= 0.1 || p_b.is_null() {
                    continue;
                }
                let mut rect = CGRect::default();
                if CGRectMakeWithDictionaryRepresentation(p_b, &mut rect) {
                    if rect.size.width >= 150.0 && rect.size.height >= 80.0 {
                        out.push(PerchWindow {
                            id: wid as u64,
                            x: rect.origin.x,
                            y: rect.origin.y,
                            w: rect.size.width,
                            h: rect.size.height,
                        });
                    }
                }
            }
            CFRelease(arr);
        }
        out
    }
}

#[cfg(target_os = "windows")]
mod os_windows {
    use super::PerchWindow;
    use std::ffi::c_void;

    #[repr(C)]
    #[derive(Default, Copy, Clone)]
    struct RECT {
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
    }

    #[repr(C)]
    #[derive(Default, Copy, Clone)]
    struct POINT {
        x: i32,
        y: i32,
    }

    #[link(name = "user32")]
    extern "system" {
        fn EnumWindows(cb: extern "system" fn(isize, isize) -> i32, lparam: isize) -> i32;
        fn IsWindow(hwnd: isize) -> i32;
        fn IsWindowVisible(hwnd: isize) -> i32;
        fn IsIconic(hwnd: isize) -> i32;
        fn GetWindowThreadProcessId(hwnd: isize, pid: *mut u32) -> u32;
        fn GetWindowRect(hwnd: isize, rect: *mut RECT) -> i32;
        fn GetWindowLongW(hwnd: isize, n_index: i32) -> i32;
        fn GetCursorPos(pt: *mut POINT) -> i32;
    }

    #[link(name = "dwmapi")]
    extern "system" {
        fn DwmGetWindowAttribute(
            hwnd: isize,
            dw_attribute: u32,
            pv_attribute: *mut c_void,
            cb_attribute: u32,
        ) -> i32;
    }

    const GWL_EXSTYLE: i32 = -20;
    const WS_EX_TOOLWINDOW: i32 = 0x0000_0080;
    const WS_EX_NOACTIVATE: i32 = 0x0800_0000;
    const DWMWA_CLOAKED: u32 = 14;

    pub fn global_cursor_logical(scale: f64) -> Option<(f64, f64)> {
        let mut pt = POINT::default();
        if unsafe { GetCursorPos(&mut pt) } != 0 {
            let s = scale.max(1.0);
            Some((pt.x as f64 / s, pt.y as f64 / s))
        } else {
            None
        }
    }

    unsafe fn is_cloaked(hwnd: isize) -> bool {
        let mut cloaked: u32 = 0;
        let hr = DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            (&mut cloaked as *mut u32).cast::<c_void>(),
            std::mem::size_of::<u32>() as u32,
        );
        hr == 0 && cloaked != 0
    }

    unsafe fn read_perch_rect(hwnd: isize, scale: f64) -> Option<PerchWindow> {
        if IsWindow(hwnd) == 0 || IsWindowVisible(hwnd) == 0 || IsIconic(hwnd) != 0 || is_cloaked(hwnd) {
            return None;
        }
        let ex = GetWindowLongW(hwnd, GWL_EXSTYLE);
        if (ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)) != 0 {
            return None;
        }
        let mut r = RECT::default();
        if GetWindowRect(hwnd, &mut r) == 0 {
            return None;
        }
        let w = (r.right - r.left) as f64 / scale;
        let h = (r.bottom - r.top) as f64 / scale;
        if w >= 150.0 && h >= 80.0 {
            Some(PerchWindow {
                id: hwnd as u64,
                x: r.left as f64 / scale,
                y: r.top as f64 / scale,
                w,
                h,
            })
        } else {
            None
        }
    }

    struct CollectCtx {
        my_pid: u32,
        scale: f64,
        list: Vec<PerchWindow>,
    }

    extern "system" fn enum_cb(hwnd: isize, lparam: isize) -> i32 {
        unsafe {
            let ctx = &mut *(lparam as *mut CollectCtx);
            let mut pid: u32 = 0;
            GetWindowThreadProcessId(hwnd, &mut pid);
            if pid == ctx.my_pid {
                return 1;
            }
            if let Some(pw) = read_perch_rect(hwnd, ctx.scale) {
                ctx.list.push(pw);
            }
            1
        }
    }

    pub fn list_visible_windows_scaled(scale: f64) -> Vec<PerchWindow> {
        let mut ctx = CollectCtx {
            my_pid: std::process::id(),
            scale: scale.max(1.0),
            list: Vec::new(),
        };
        unsafe {
            EnumWindows(enum_cb, (&mut ctx as *mut CollectCtx) as isize);
        }
        ctx.list
    }

    pub fn query_single_window_scaled(id: u64, scale: f64) -> Option<PerchWindow> {
        unsafe { read_perch_rect(id as isize, scale.max(1.0)) }
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
mod os_windows {
    use super::PerchWindow;
    pub fn list_visible_windows() -> Vec<PerchWindow> {
        Vec::new()
    }
    pub fn global_cursor_logical(_scale: f64) -> Option<(f64, f64)> {
        None
    }
}

fn query_global_cursor(scale_factor: f64) -> Option<(f64, f64)> {
    #[cfg(target_os = "macos")]
    {
        let _ = scale_factor;
        os_windows::global_cursor_logical()
    }
    #[cfg(target_os = "windows")]
    {
        os_windows::global_cursor_logical(scale_factor)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        os_windows::global_cursor_logical(scale_factor)
    }
}

fn query_os_windows(scale_factor: f64) -> Vec<PerchWindow> {
    #[cfg(target_os = "macos")]
    {
        let _ = scale_factor;
        os_windows::list_visible_windows()
    }
    #[cfg(target_os = "windows")]
    {
        os_windows::list_visible_windows_scaled(scale_factor)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = scale_factor;
        os_windows::list_visible_windows()
    }
}

fn query_live_perched_window(id: u64, cached_wins: &[PerchWindow], scale_factor: f64) -> Option<(usize, PerchWindow)> {
    let idx = cached_wins.iter().position(|w| w.id == id);
    #[cfg(target_os = "windows")]
    {
        let live = os_windows::query_single_window_scaled(id, scale_factor);
        return live.map(|w| (idx.unwrap_or(0), w));
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = scale_factor;
        idx.map(|i| (i, cached_wins[i].clone()))
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StepInput {
    dt: f32,
    mx: f32,
    my: f32,
    hover: bool,
    pet: bool,
    poke: bool,
    dblclick: bool,
    grabbing: bool,
    drag_vx: f32,
    resizing: bool,
    menu_open: bool,
    auto_mode: bool,
    force_state: String,
    style: String,
    outline: bool,
    last_pal_ver: u32,
}

#[derive(Serialize)]
struct PythonReqOwned {
    cmd: String,
    dt: f32,
    mx: f32,
    my: f32,
    hover: bool,
    pet: bool,
    poke: bool,
    dblclick: bool,
    grabbing: bool,
    drag_vx: f32,
    airborne: bool,
    just_landed: bool,
    just_perched: bool,
    on_window_edge: bool,
    resizing: bool,
    scale_factor: f32,
    intro_big: bool,
    intro_target_ratio: f32,
    hit_edge: i32,
    auto_mode: bool,
    force_state: String,
    style: String,
    outline: bool,
    last_pal_ver: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    path: Option<String>,
}

struct DropInbox {
    drag_hover_file: bool,
    pending_cmd: Option<(String, Option<String>)>,
}
struct DropState(Mutex<DropInbox>);

struct PythonPipe {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<ChildStdout>>,
}

impl PythonPipe {
    fn kill(&mut self) {
        if let Some(mut stdin) = self.stdin.take() {
            let _ = stdin.write_all(b"{\"cmd\":\"quit\"}\n");
            let _ = stdin.flush();
        }
        self.stdout.take();
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for PythonPipe {
    fn drop(&mut self) {
        self.kill();
    }
}

struct PipeState(Arc<Mutex<PythonPipe>>);

struct MascotLayout {
    win_x: f64,
    win_y: f64,
    win_w: f64,
    win_h: f64,
    normal_w: f64,
    normal_h: f64,
    pre_big_h: Option<f64>,
    vel_y: f64,
    last_vx: f64,
    last_state: String,
    airborne: bool,
    just_landed: bool,
    just_perched: bool,
    perch_enabled: bool,
    perch_wid: Option<u64>,
    perch_last_x: f64,
    perch_last_y: f64,
    perch_occluded_ticks: u32,
    can_perch_preview: bool,
    intro_enabled: bool,
    intro_active: bool,
    intro_big_w: f64,
    intro_big_h: f64,
    initialized_pos: bool,
    cached_wins: Vec<PerchWindow>,
    last_win_scan: Instant,
    click_through_enabled: bool,
    last_ignore_cursor: bool,
    alpha_mask: Vec<u8>,
}
struct LayoutState(Mutex<MascotLayout>);

fn candidate_roots(app: &tauri::AppHandle) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        roots.push(res);
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..8 {
            if let Some(p) = cur {
                roots.push(p.clone());
                cur = p.parent().map(|x| x.to_path_buf());
            }
        }
    }
    let mut cur = Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")));
    for _ in 0..8 {
        if let Some(p) = cur {
            roots.push(p.clone());
            cur = p.parent().map(|x| x.to_path_buf());
        }
    }
    roots
}

fn find_bundled_engine(roots: &[PathBuf]) -> Option<PathBuf> {
    let exe_name = if cfg!(windows) {
        "vrm_walk_engine.exe"
    } else {
        "vrm_walk_engine"
    };
    for root in roots {
        for rel in [
            format!("engine_dist/vrm_walk_engine/{exe_name}"),
            format!("src-tauri/engine_dist/vrm_walk_engine/{exe_name}"),
        ] {
            let cand = root.join(rel);
            if cand.exists() {
                return Some(cand);
            }
        }
    }
    None
}

fn find_dev_python(roots: &[PathBuf]) -> String {
    if let Ok(explicit) = std::env::var("VRM_WALK_PYTHON") {
        if !explicit.trim().is_empty() && Path::new(&explicit).exists() {
            return explicit;
        }
    }
    for root in roots {
        for rel in [".venv/bin/python", ".venv/Scripts/python.exe"] {
            let cand = root.join(rel);
            if cand.exists() {
                return cand.to_string_lossy().into_owned();
            }
        }
    }
    if cfg!(windows) {
        "python".to_string()
    } else {
        "python3".to_string()
    }
}

fn find_script_and_sample(roots: &[PathBuf]) -> (PathBuf, Option<PathBuf>) {
    let mut script = PathBuf::from("engine/walk_engine.py");
    for b in roots {
        let s = b.join("engine/walk_engine.py");
        if s.exists() {
            script = s;
            break;
        }
    }
    // Look for the sample on its own, in root order: the bundled resource folder comes first,
    // so an installed app never reaches back into the source folder it was built from
    // (on macOS that folder may be in Downloads/Desktop and trigger a privacy prompt).
    let mut sample = None;
    for b in roots {
        let sv = b.join("assets/sample.vrm");
        if sv.exists() {
            sample = Some(sv);
            break;
        }
    }
    (script, sample)
}

fn spawn_python_worker(
    app: &tauri::AppHandle,
    initial_vrm_override: Option<&str>,
) -> Result<(Child, ChildStdin, BufReader<ChildStdout>), String> {
    let roots = candidate_roots(app);
    let (script, sample) = find_script_and_sample(&roots);
    let initial_vrm = initial_vrm_override
        .filter(|p| Path::new(p).exists())
        .map(PathBuf::from)
        .or(sample.clone());

    let (program, mut cmd) = if let Some(bin_path) = find_bundled_engine(&roots) {
        let mut c = Command::new(&bin_path);
        c.arg("--ipc");
        if let Some(sp) = &sample {
            c.arg("--sample").arg(sp);
        }
        if let Some(sv) = initial_vrm {
            c.arg(sv);
        }
        (bin_path.to_string_lossy().into_owned(), c)
    } else {
        let py = find_dev_python(&roots);
        let mut c = Command::new(&py);
        c.arg("-u").arg(&script).arg("--ipc");
        if let Some(sp) = &sample {
            c.arg("--sample").arg(sp);
        }
        if let Some(sv) = initial_vrm {
            c.arg(sv);
        }
        (py, c)
    };

    cmd.env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn mascot engine ({program}): {e}"))?;
    let stdin = child.stdin.take().ok_or("Missing worker stdin")?;
    let stdout = child.stdout.take().ok_or("Missing worker stdout")?;
    Ok((child, stdin, BufReader::new(stdout)))
}

fn monitor_work_area(window: &Window) -> (f64, f64, f64, f64, f64) {
    if let Ok(Some(mon)) = window.current_monitor() {
        let sf = mon.scale_factor().max(1.0);
        let wa = mon.work_area();
        return (
            wa.position.x as f64 / sf,
            wa.position.y as f64 / sf,
            wa.size.width as f64 / sf,
            wa.size.height as f64 / sf,
            sf,
        );
    }
    (0.0, 0.0, 1280.0, 720.0, 1.0)
}

fn floor_y_for(wa_y: f64, wa_h: f64, win_h: f64) -> f64 {
    (wa_y + wa_h - win_h + win_h * 0.06).max(wa_y)
}

fn standing_foot_y(win_y: f64, win_h: f64) -> f64 {
    win_y + win_h * 0.89
}

fn edge_window_top_y(w: &PerchWindow, win_h: f64, is_sitting: bool) -> f64 {
    let anchor = if is_sitting { 0.69 } else { 0.89 };
    w.y - win_h * anchor
}

fn refresh_windows_if_needed(st: &mut MascotLayout, sf: f64) {
    let interval_ms = if cfg!(target_os = "macos") && st.perch_wid.is_some() {
        33
    } else {
        120
    };
    if st.last_win_scan.elapsed() >= Duration::from_millis(interval_ms) {
        st.cached_wins = query_os_windows(sf);
        st.last_win_scan = Instant::now();
    }
}

fn is_edge_visible(wins: &[PerchWindow], idx: usize, px: f64, py: f64) -> bool {
    for higher in &wins[..idx] {
        if px >= higher.x + 4.0
            && px <= higher.x + higher.w - 4.0
            && py + 6.0 >= higher.y
            && py + 6.0 <= higher.y + higher.h
        {
            return false;
        }
    }
    true
}

fn find_perch_candidate<'a>(
    wins: &'a [PerchWindow],
    wa_y: f64,
    foot_x: f64,
    foot_y: f64,
    win_h: f64,
    max_dist: f64,
) -> Option<&'a PerchWindow> {
    let mut best: Option<(&PerchWindow, f64)> = None;
    for (idx, w) in wins.iter().enumerate() {
        if w.y < wa_y + win_h * 0.48 {
            continue;
        }
        if foot_x < w.x + 18.0 || foot_x > w.x + w.w - 18.0 {
            continue;
        }
        let d = (foot_y - w.y).abs();
        if d <= max_dist && is_edge_visible(wins, idx, foot_x, w.y) {
            if best.map(|(_, bd)| d < bd).unwrap_or(true) {
                best = Some((w, d));
            }
        }
    }
    best.map(|(w, _)| w)
}

fn cursor_hits_mascot(st: &MascotLayout, global_cursor: Option<(f64, f64)>) -> bool {
    let Some((gx, gy)) = global_cursor else {
        return true;
    };
    if gx < st.win_x || gx >= st.win_x + st.win_w || gy < st.win_y || gy >= st.win_y + st.win_h {
        return false;
    }
    if st.alpha_mask.len() != FRAME_W * FRAME_H {
        return true;
    }
    let px = (((gx - st.win_x) / st.win_w.max(1.0)) * FRAME_W as f64) as isize;
    let py = (((gy - st.win_y) / st.win_h.max(1.0)) * FRAME_H as f64) as isize;
    // Keep top-right resize grip box clickable
    if px >= (FRAME_W as isize - 26) && py <= 26 {
        return true;
    }
    // Check a comfortable 6-px sprite halo so borders/drops catch effortlessly
    let rad: isize = 6;
    let y0 = (py - rad).max(0) as usize;
    let y1 = (py + rad).min(FRAME_H as isize - 1) as usize;
    let x0 = (px - rad).max(0) as usize;
    let x1 = (px + rad).min(FRAME_W as isize - 1) as usize;
    for yy in y0..=y1 {
        let row = yy * FRAME_W;
        for xx in x0..=x1 {
            if st.alpha_mask[row + xx] != 255 {
                return true;
            }
        }
    }
    false
}

fn apply_win_geometry(window: &Window, resize_to: Option<(f64, f64)>, move_to: Option<(f64, f64)>) {
    if let Some((w, h)) = resize_to {
        let _ = window.set_size(Size::Logical(LogicalSize { width: w, height: h }));
    }
    if let Some((x, y)) = move_to {
        let _ = window.set_position(Position::Logical(LogicalPosition::new(x, y)));
    }
}

fn prepare_intro_big(
    st: &mut MascotLayout,
    wa_x: f64,
    wa_y: f64,
    wa_w: f64,
    wa_h: f64,
) -> (Option<(f64, f64)>, Option<(f64, f64)>) {
    let big_h = (wa_h * 0.78).clamp(380.0, 740.0).round();
    let big_w = (big_h * ASPECT_RATIO).round();
    st.intro_big_w = big_w;
    st.intro_big_h = big_h;
    st.win_w = big_w;
    st.win_h = big_h;
    st.perch_wid = None;
    st.airborne = false;
    st.vel_y = 0.0;
    st.intro_active = true;
    st.win_x = (wa_x + (wa_w - big_w) * 0.5).round();
    st.win_y = floor_y_for(wa_y, wa_h, big_h).round();
    (Some((big_w, big_h)), Some((st.win_x, st.win_y)))
}

fn prepare_finish_intro_big(
    st: &mut MascotLayout,
    wa_x: f64,
    wa_y: f64,
    wa_w: f64,
    wa_h: f64,
) -> (Option<(f64, f64)>, Option<(f64, f64)>) {
    let nw = st.normal_w;
    let nh = st.normal_h;
    let cx = st.win_x + st.win_w * 0.5;
    st.intro_active = false;
    st.win_w = nw;
    st.win_h = nh;
    st.win_x = (cx - nw * 0.5).clamp(wa_x, (wa_x + wa_w - nw).max(wa_x));
    st.win_y = floor_y_for(wa_y, wa_h, nh);
    st.just_landed = true;
    (Some((nw, nh)), Some((st.win_x, st.win_y)))
}

fn send_python_step(pipe: &mut PythonPipe, req: &PythonReqOwned) -> Result<(Vec<u8>, Vec<u8>), String> {
    let stdin = pipe.stdin.as_mut().ok_or("Python worker not running")?;
    let stdout = pipe.stdout.as_mut().ok_or("Python worker stdout closed")?;

    let mut line = serde_json::to_string(req).map_err(|e| e.to_string())?;
    line.push('\n');
    stdin.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
    stdin.flush().map_err(|e| e.to_string())?;

    let mut magic = [0u8; 4];
    stdout.read_exact(&mut magic).map_err(|e| format!("Worker read header: {e}"))?;
    if &magic != b"VRMW" {
        let mut rest = String::new();
        let _ = stdout.read_line(&mut rest);
        return Err(format!("Unexpected worker magic: {:?} {}", magic, rest));
    }

    let mut len_buf = [0u8; 4];
    stdout.read_exact(&mut len_buf).map_err(|e| e.to_string())?;
    let meta_len = u32::from_le_bytes(len_buf) as usize;
    let mut meta_bytes = vec![0u8; meta_len];
    stdout.read_exact(&mut meta_bytes).map_err(|e| e.to_string())?;

    stdout.read_exact(&mut len_buf).map_err(|e| e.to_string())?;
    let pix_len = u32::from_le_bytes(len_buf) as usize;
    let mut pix_bytes = vec![0u8; pix_len];
    stdout.read_exact(&mut pix_bytes).map_err(|e| e.to_string())?;

    Ok((meta_bytes, pix_bytes))
}

#[tauri::command]
async fn step_mascot(
    window: Window,
    drop_state: State<'_, DropState>,
    layout_state: State<'_, LayoutState>,
    pipe_state: State<'_, PipeState>,
    settings_state: State<'_, SettingsState>,
    input: StepInput,
) -> Result<Response, String> {
    let (wa_x, wa_y, wa_w, wa_h, sf) = monitor_work_area(&window);
    let global_cursor = query_global_cursor(sf);

    let (drag_hover_file, pending_cmd) = {
        let mut inbox = drop_state.0.lock().map_err(|e| e.to_string())?;
        (inbox.drag_hover_file, inbox.pending_cmd.take())
    };
    let loaded_vrm_candidate = match &pending_cmd {
        Some((cmd, Some(p))) if cmd == "load_vrm" => Some(p.clone()),
        _ => None,
    };
    let reset_sample_requested = matches!(&pending_cmd, Some((cmd, _)) if cmd == "reset_sample");

    let (req, resize_to, move_to) = {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        refresh_windows_if_needed(&mut st, sf);

        let mut resize_to: Option<(f64, f64)> = None;
        let mut move_to: Option<(f64, f64)> = None;

        if !st.initialized_pos {
            st.initialized_pos = true;
            if st.intro_enabled {
                (resize_to, move_to) = prepare_intro_big(&mut st, wa_x, wa_y, wa_w, wa_h);
            } else {
                let max_x = (wa_x + wa_w - st.win_w).max(wa_x);
                st.win_x = (max_x - 80.0).max(wa_x);
                st.win_y = floor_y_for(wa_y, wa_h, st.win_h);
                resize_to = Some((st.win_w, st.win_h));
                move_to = Some((st.win_x, st.win_y));
            }
        }

        if let Some((ref cmd_name, _)) = pending_cmd {
            if (cmd_name == "load_vrm" || cmd_name == "reset_sample" || cmd_name == "replay_intro")
                && st.intro_enabled
                && !st.intro_active
            {
                let (rz, mv) = prepare_intro_big(&mut st, wa_x, wa_y, wa_w, wa_h);
                if rz.is_some() {
                    resize_to = rz;
                }
                if mv.is_some() {
                    move_to = mv;
                }
            }
        }

        let dt = (input.dt as f64).clamp(0.001, 0.08);
        let mut hit_edge = 0i32;

        if !st.intro_active && !input.grabbing && !input.resizing {
            let floor_y = floor_y_for(wa_y, wa_h, st.win_h);
            let mon_min_x = wa_x;
            let mon_max_x = (wa_x + wa_w - st.win_w).max(wa_x);

            if st.airborne {
                let prev_foot_y = standing_foot_y(st.win_y, st.win_h);
                st.vel_y += 1450.0 * dt;
                st.win_y += st.vel_y * dt;
                let new_foot_y = standing_foot_y(st.win_y, st.win_h);
                let foot_x = st.win_x + st.win_w * 0.5;

                let mut landed_on_win = false;
                if st.perch_enabled && st.vel_y > 0.0 {
                    let hit_w = st
                        .cached_wins
                        .iter()
                        .enumerate()
                        .find(|(idx, w)| {
                            w.y >= wa_y + st.win_h * 0.48
                                && foot_x >= w.x + 18.0
                                && foot_x <= w.x + w.w - 18.0
                                && prev_foot_y <= w.y + 10.0
                                && new_foot_y >= w.y - 2.0
                                && is_edge_visible(&st.cached_wins, *idx, foot_x, w.y)
                        })
                        .map(|(_, w)| w.clone());
                    if let Some(w) = hit_w {
                        st.perch_wid = Some(w.id);
                        st.perch_last_x = w.x;
                        st.perch_last_y = w.y;
                        st.perch_occluded_ticks = 0;
                        st.win_y = edge_window_top_y(&w, st.win_h, true);
                        st.vel_y = 0.0;
                        st.airborne = false;
                        st.just_perched = true;
                        landed_on_win = true;
                    }
                }
                if !landed_on_win && st.win_y >= floor_y {
                    st.win_y = floor_y;
                    st.vel_y = 0.0;
                    st.airborne = false;
                    st.just_landed = true;
                    st.perch_wid = None;
                }
            } else if let Some(pid) = st.perch_wid {
                if let Some((idx, w)) = query_live_perched_window(pid, &st.cached_wins, sf) {
                    let host_dx = w.x - st.perch_last_x;
                    let host_dy = w.y - st.perch_last_y;
                    st.perch_last_x = w.x;
                    st.perch_last_y = w.y;
                    st.win_x += host_dx + st.last_vx * dt;
                    st.win_y += host_dy;

                    let left_lim = w.x.max(mon_min_x);
                    let right_lim = (w.x + w.w - st.win_w).min(mon_max_x).max(left_lim);
                    if st.win_x <= left_lim {
                        st.win_x = left_lim;
                        hit_edge = -1;
                    } else if st.win_x >= right_lim {
                        st.win_x = right_lim;
                        hit_edge = 1;
                    }
                    let is_sit = st.last_state == "sit" || st.last_state == "nap";
                    let target_y = edge_window_top_y(&w, st.win_h, is_sit).clamp(wa_y, floor_y);
                    st.win_y += (target_y - st.win_y) * (dt * 14.0).min(1.0);

                    let foot_x = st.win_x + st.win_w * 0.5;
                    let occluded = !is_edge_visible(&st.cached_wins, idx, foot_x, w.y);
                    st.perch_occluded_ticks = if occluded { st.perch_occluded_ticks + 1 } else { 0 };

                    if !st.perch_enabled || w.y < wa_y + 18.0 || st.perch_occluded_ticks >= 6 {
                        st.perch_wid = None;
                        st.perch_occluded_ticks = 0;
                        st.airborne = true;
                    }
                } else {
                    st.perch_wid = None;
                    st.perch_occluded_ticks = 0;
                    st.airborne = true;
                }
            } else {
                st.win_x += st.last_vx * dt;
                if st.win_x <= mon_min_x {
                    st.win_x = mon_min_x;
                    hit_edge = -1;
                } else if st.win_x >= mon_max_x {
                    st.win_x = mon_max_x;
                    hit_edge = 1;
                }
                st.win_y = floor_y;
            }
            move_to = Some((st.win_x, st.win_y));
        }

        // Desktop-wide cursor look-at fallback when mouse is outside mascot (during click-through / desktop use)
        let (eff_hover, eff_mx, eff_my) = if input.hover {
            (true, input.mx, input.my)
        } else if let Some((gx, gy)) = global_cursor {
            let head_cx = st.win_x + st.win_w * 0.5;
            let head_cy = st.win_y + st.win_h * 0.28;
            let lx = ((gx - head_cx) / 420.0).clamp(-1.0, 1.0) as f32;
            let ly = ((head_cy - gy) / 320.0).clamp(-1.0, 1.0) as f32;
            (true, lx, ly)
        } else {
            (false, 0.0, 0.0)
        };

        let (cmd_str, path_arg) = match pending_cmd {
            Some((c, p)) => (c, p),
            None => ("step".to_string(), None),
        };

        let ratio = (st.normal_h / st.intro_big_h.max(1.0)) as f32;
        let on_window_edge = st.perch_wid.is_some();
        let scale_factor = (st.win_h / BASE_H) as f32;

        let req = PythonReqOwned {
            cmd: cmd_str,
            dt: dt as f32,
            mx: eff_mx,
            my: eff_my,
            hover: eff_hover,
            pet: input.pet,
            poke: input.poke,
            dblclick: input.dblclick,
            grabbing: input.grabbing,
            drag_vx: input.drag_vx,
            airborne: st.airborne,
            just_landed: st.just_landed,
            just_perched: st.just_perched,
            on_window_edge,
            resizing: input.resizing,
            scale_factor,
            intro_big: st.intro_active,
            intro_target_ratio: ratio,
            hit_edge,
            auto_mode: input.auto_mode,
            force_state: input.force_state,
            style: input.style,
            outline: input.outline,
            last_pal_ver: input.last_pal_ver,
            path: path_arg,
        };
        st.just_landed = false;
        st.just_perched = false;
        (req, resize_to, move_to)
    };

    apply_win_geometry(&window, resize_to, move_to);

    let pipe_arc = Arc::clone(&pipe_state.0);
    let (meta_bytes, pix_bytes) = tauri::async_runtime::spawn_blocking(move || {
        let mut pipe = pipe_arc.lock().map_err(|e| e.to_string())?;
        send_python_step(&mut pipe, &req)
    })
    .await
    .map_err(|e| format!("IPC task panicked: {e}"))??;

    let mut meta_val: Value = serde_json::from_slice(&meta_bytes).unwrap_or(Value::Null);
    let status_error = meta_val.get("statusError").and_then(|v| v.as_bool()).unwrap_or(false);
    let intro_done = meta_val.get("introDone").and_then(|v| v.as_bool()).unwrap_or(false);

    // Persist newly loaded VRM path only when Python confirmed it succeeded (not license-blocked)
    if !status_error {
        if let Some(vrm_path) = loaded_vrm_candidate {
            if let Ok(mut s) = settings_state.data.lock() {
                s.last_vrm_path = Some(vrm_path);
                let clone = s.clone();
                drop(s);
                settings_state.persist(&clone);
            }
        } else if reset_sample_requested {
            if let Ok(mut s) = settings_state.data.lock() {
                s.last_vrm_path = None;
                let clone = s.clone();
                drop(s);
                settings_state.persist(&clone);
            }
        }
    }

    let (post_resize, post_move, ignore_update, snap) = {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        let mut prz = None;
        let mut pmv = None;
        if intro_done && st.intro_active {
            (prz, pmv) = prepare_finish_intro_big(&mut st, wa_x, wa_y, wa_w, wa_h);
        }
        if let Some(vx) = meta_val.get("vx").and_then(|v| v.as_f64()) {
            st.last_vx = vx;
        }
        if let Some(s) = meta_val.get("state").and_then(|v| v.as_str()) {
            st.last_state = s.to_string();
        }
        if pix_bytes.len() == FRAME_W * FRAME_H {
            st.alpha_mask.resize(pix_bytes.len(), 255);
            st.alpha_mask.copy_from_slice(&pix_bytes);
        }

        // Pixel-accurate click-through evaluation outside transparent margins
        let want_ignore = st.click_through_enabled
            && !input.menu_open
            && !input.grabbing
            && !input.resizing
            && !drag_hover_file
            && !st.intro_active
            && !cursor_hits_mascot(&st, global_cursor);

        let ignore_update = if want_ignore != st.last_ignore_cursor {
            st.last_ignore_cursor = want_ignore;
            Some(want_ignore)
        } else {
            None
        };

        (
            prz,
            pmv,
            ignore_update,
            (
                st.airborne,
                st.perch_wid.is_some(),
                st.can_perch_preview,
                st.win_h.round() as i64,
                st.intro_active,
            ),
        )
    };
    apply_win_geometry(&window, post_resize, post_move);
    if let Some(ignore) = ignore_update {
        let _ = window.set_ignore_cursor_events(ignore);
    }

    if let Some(obj) = meta_val.as_object_mut() {
        obj.insert("dragHoverFile".into(), Value::Bool(drag_hover_file));
        obj.insert("airborne".into(), Value::Bool(snap.0));
        obj.insert("onWindowEdge".into(), Value::Bool(snap.1));
        obj.insert("canPerchPreview".into(), Value::Bool(snap.2));
        obj.insert("winH".into(), Value::from(snap.3));
        obj.insert("introBig".into(), Value::Bool(snap.4));
    }
    let out_meta = serde_json::to_vec(&meta_val).unwrap_or(meta_bytes);

    let mut packet = Vec::with_capacity(4 + out_meta.len() + pix_bytes.len());
    packet.extend_from_slice(&(out_meta.len() as u32).to_le_bytes());
    packet.extend_from_slice(&out_meta);
    packet.extend_from_slice(&pix_bytes);
    Ok(Response::new(packet))
}

#[tauri::command]
fn get_saved_settings(settings_state: State<SettingsState>) -> Result<SavedSettings, String> {
    let s = settings_state.data.lock().map_err(|e| e.to_string())?;
    Ok(s.clone())
}

#[tauri::command]
fn save_ui_prefs(
    settings_state: State<SettingsState>,
    style: String,
    outline: bool,
    sfx_on: bool,
) -> Result<(), String> {
    let snap = {
        let mut s = settings_state.data.lock().map_err(|e| e.to_string())?;
        s.style = style;
        s.outline = outline;
        s.sfx_on = sfx_on;
        s.clone()
    };
    settings_state.persist(&snap);
    Ok(())
}

#[tauri::command]
fn get_window_pos(layout_state: State<LayoutState>) -> Result<(f64, f64), String> {
    let st = layout_state.0.lock().map_err(|e| e.to_string())?;
    Ok((st.win_x, st.win_y))
}

#[tauri::command]
fn move_mascot_to(
    window: Window,
    layout_state: State<LayoutState>,
    x: f64,
    y: f64,
) -> Result<(), String> {
    let (wa_x, wa_y, wa_w, wa_h, sf) = monitor_work_area(&window);
    let move_target = {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        if st.intro_active {
            return Ok(());
        }
        refresh_windows_if_needed(&mut st, sf);
        let max_x = (wa_x + wa_w - st.win_w).max(wa_x);
        let floor_y = floor_y_for(wa_y, wa_h, st.win_h);
        st.win_x = x.clamp(wa_x, max_x);
        st.win_y = y.clamp(wa_y, floor_y);
        st.perch_wid = None;
        st.airborne = st.win_y < floor_y - 2.0;
        st.vel_y = 0.0;

        let foot_x = st.win_x + st.win_w * 0.5;
        let foot_y = standing_foot_y(st.win_y, st.win_h);
        st.can_perch_preview = st.perch_enabled
            && find_perch_candidate(&st.cached_wins, wa_y, foot_x, foot_y, st.win_h, 36.0).is_some();
        (st.win_x, st.win_y)
    };
    window
        .set_position(Position::Logical(LogicalPosition::new(move_target.0, move_target.1)))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn release_grab(window: Window, layout_state: State<LayoutState>) -> Result<(), String> {
    let (wa_x, wa_y, wa_w, wa_h, sf) = monitor_work_area(&window);
    let move_target = {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        refresh_windows_if_needed(&mut st, sf);
        st.can_perch_preview = false;
        st.vel_y = 0.0;

        let foot_x = st.win_x + st.win_w * 0.5;
        let foot_y = standing_foot_y(st.win_y, st.win_h);
        let mut mv = None;
        if st.perch_enabled {
            if let Some(w) = find_perch_candidate(&st.cached_wins, wa_y, foot_x, foot_y, st.win_h, 38.0).cloned() {
                let max_x = (wa_x + wa_w - st.win_w).max(wa_x);
                st.perch_wid = Some(w.id);
                st.perch_last_x = w.x;
                st.perch_last_y = w.y;
                st.perch_occluded_ticks = 0;
                st.win_x = st.win_x.clamp(w.x.max(wa_x), (w.x + w.w - st.win_w).min(max_x).max(w.x));
                st.win_y = edge_window_top_y(&w, st.win_h, true).max(wa_y);
                st.airborne = false;
                st.just_perched = true;
                mv = Some((st.win_x, st.win_y));
            }
        }
        if mv.is_none() {
            let floor_y = floor_y_for(wa_y, wa_h, st.win_h);
            st.airborne = st.win_y < floor_y - 2.0;
        }
        mv
    };
    apply_win_geometry(&window, None, move_target);
    Ok(())
}

#[tauri::command]
fn set_mascot_height(
    window: Window,
    layout_state: State<LayoutState>,
    settings_state: State<SettingsState>,
    height: f64,
    persist_pref: Option<bool>,
) -> Result<f64, String> {
    let (wa_x, wa_y, wa_w, wa_h, _) = monitor_work_area(&window);
    let (new_w, new_h, wx, wy) = {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        if st.intro_active {
            return Ok(st.win_h);
        }
        let max_h = (wa_h * 0.80).clamp(320.0, 820.0);
        let new_h = height.clamp(136.0, max_h).round();
        let new_w = (new_h * ASPECT_RATIO).round();

        let cx = st.win_x + st.win_w * 0.5;
        st.win_w = new_w;
        st.win_h = new_h;
        st.normal_w = new_w;
        st.normal_h = new_h;

        let max_x = (wa_x + wa_w - new_w).max(wa_x);
        st.win_x = (cx - new_w * 0.5).clamp(wa_x, max_x);
        let floor_y = floor_y_for(wa_y, wa_h, new_h);
        if let Some(pid) = st.perch_wid {
            if let Some(w) = st.cached_wins.iter().find(|w| w.id == pid).cloned() {
                let is_sit = st.last_state == "sit" || st.last_state == "nap";
                st.win_y = edge_window_top_y(&w, new_h, is_sit).clamp(wa_y, floor_y);
            } else {
                st.win_y = floor_y;
                st.perch_wid = None;
            }
        } else {
            st.win_y = floor_y;
        }
        (new_w, new_h, st.win_x, st.win_y)
    };

    window
        .set_size(Size::Logical(LogicalSize { width: new_w, height: new_h }))
        .map_err(|e| e.to_string())?;
    let _ = window.set_position(Position::Logical(LogicalPosition::new(wx, wy)));

    if persist_pref.unwrap_or(true) {
        if let Ok(mut s) = settings_state.data.lock() {
            s.win_h = new_h;
            let clone = s.clone();
            drop(s);
            settings_state.persist(&clone);
        }
    }
    Ok(new_h)
}

#[tauri::command]
fn toggle_big_view(
    window: Window,
    layout_state: State<LayoutState>,
    settings_state: State<SettingsState>,
) -> Result<f64, String> {
    let (_, _, _, wa_h, _) = monitor_work_area(&window);
    let target_h = {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        let big_h = (wa_h * 0.76).clamp(380.0, 760.0).round();
        if let Some(prev) = st.pre_big_h.take() {
            prev
        } else {
            st.pre_big_h = Some(st.normal_h);
            big_h
        }
    };
    set_mascot_height(window, layout_state, settings_state, target_h, Some(false))
}

#[tauri::command]
fn save_temp_vrm(
    drop_state: State<DropState>,
    settings_state: State<SettingsState>,
    name: String,
    bytes: Vec<u8>,
) -> Result<(), String> {
    let inbox = settings_state.config_dir.join("avatars");
    std::fs::create_dir_all(&inbox).map_err(|e| e.to_string())?;
    let safe: String = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-' { c } else { '_' })
        .collect();
    let target = inbox.join(format!("uploaded_{}", safe));
    std::fs::write(&target, &bytes).map_err(|e| e.to_string())?;
    let mut inbox_st = drop_state.0.lock().map_err(|e| e.to_string())?;
    inbox_st.pending_cmd = Some(("load_vrm".to_string(), Some(target.to_string_lossy().into_owned())));
    Ok(())
}

#[tauri::command]
fn reset_sample_vrm(drop_state: State<DropState>) -> Result<(), String> {
    let mut inbox_st = drop_state.0.lock().map_err(|e| e.to_string())?;
    inbox_st.pending_cmd = Some(("reset_sample".to_string(), None));
    Ok(())
}

#[tauri::command]
fn replay_intro(drop_state: State<DropState>) -> Result<(), String> {
    let mut inbox_st = drop_state.0.lock().map_err(|e| e.to_string())?;
    inbox_st.pending_cmd = Some(("replay_intro".to_string(), None));
    Ok(())
}

#[tauri::command]
fn set_intro_enabled(
    layout_state: State<LayoutState>,
    settings_state: State<SettingsState>,
    enabled: bool,
) -> Result<(), String> {
    {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        st.intro_enabled = enabled;
    }
    let snap = {
        let mut s = settings_state.data.lock().map_err(|e| e.to_string())?;
        s.intro_enabled = enabled;
        s.clone()
    };
    settings_state.persist(&snap);
    Ok(())
}

#[tauri::command]
fn set_perch_enabled(
    layout_state: State<LayoutState>,
    settings_state: State<SettingsState>,
    enabled: bool,
) -> Result<(), String> {
    {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        st.perch_enabled = enabled;
        if !enabled && st.perch_wid.is_some() {
            st.perch_wid = None;
            st.airborne = true;
        }
    }
    let snap = {
        let mut s = settings_state.data.lock().map_err(|e| e.to_string())?;
        s.perch_enabled = enabled;
        s.clone()
    };
    settings_state.persist(&snap);
    Ok(())
}

#[tauri::command]
fn set_click_through(
    window: Window,
    layout_state: State<LayoutState>,
    settings_state: State<SettingsState>,
    enabled: bool,
) -> Result<(), String> {
    {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        st.click_through_enabled = enabled;
        if !enabled {
            st.last_ignore_cursor = false;
        }
    }
    if !enabled {
        let _ = window.set_ignore_cursor_events(false);
    }
    let snap = {
        let mut s = settings_state.data.lock().map_err(|e| e.to_string())?;
        s.click_through = enabled;
        s.clone()
    };
    settings_state.persist(&snap);
    Ok(())
}

#[tauri::command]
fn drop_to_floor(layout_state: State<LayoutState>) -> Result<(), String> {
    let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
    if st.perch_wid.take().is_some() {
        st.airborne = true;
        st.vel_y = 0.0;
    }
    Ok(())
}

#[tauri::command]
fn set_pin_top(
    window: Window,
    settings_state: State<SettingsState>,
    pinned: bool,
) -> Result<(), String> {
    window.set_always_on_top(pinned).map_err(|e| e.to_string())?;
    let snap = {
        let mut s = settings_state.data.lock().map_err(|e| e.to_string())?;
        s.pinned = pinned;
        s.clone()
    };
    settings_state.persist(&snap);
    Ok(())
}

#[tauri::command]
fn recenter_mascot(window: Window, layout_state: State<LayoutState>) -> Result<(), String> {
    let (wa_x, wa_y, wa_w, wa_h, _) = monitor_work_area(&window);
    let (wx, wy) = {
        let mut st = layout_state.0.lock().map_err(|e| e.to_string())?;
        st.perch_wid = None;
        st.win_x = wa_x + (wa_w - st.win_w) * 0.5;
        st.win_y = floor_y_for(wa_y, wa_h, st.win_h);
        st.airborne = false;
        st.vel_y = 0.0;
        st.last_ignore_cursor = false;
        (st.win_x, st.win_y)
    };
    let _ = window.set_ignore_cursor_events(false);
    let _ = window.set_position(Position::Logical(LogicalPosition::new(wx, wy)));
    let _ = window.show();
    let _ = window.set_focus();
    Ok(())
}

#[tauri::command]
fn close_walk(app: tauri::AppHandle, window: Window, pipe_state: State<PipeState>) -> Result<(), String> {
    if let Ok(mut pipe) = pipe_state.0.lock() {
        pipe.kill();
    };
    let _ = window.close();
    app.exit(0);
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(DropState(Mutex::new(DropInbox {
            drag_hover_file: false,
            pending_cmd: None,
        })))
        .manage(PipeState(Arc::new(Mutex::new(PythonPipe {
            child: None,
            stdin: None,
            stdout: None,
        }))))
        .manage(LayoutState(Mutex::new(MascotLayout {
            win_x: 200.0,
            win_y: 400.0,
            win_w: BASE_W,
            win_h: BASE_H,
            normal_w: BASE_W,
            normal_h: BASE_H,
            pre_big_h: None,
            vel_y: 0.0,
            last_vx: 0.0,
            last_state: "idle".to_string(),
            airborne: false,
            just_landed: false,
            just_perched: false,
            perch_enabled: true,
            perch_wid: None,
            perch_last_x: 0.0,
            perch_last_y: 0.0,
            perch_occluded_ticks: 0,
            can_perch_preview: false,
            intro_enabled: true,
            intro_active: false,
            intro_big_w: BASE_W,
            intro_big_h: BASE_H,
            initialized_pos: false,
            cached_wins: Vec::new(),
            last_win_scan: Instant::now() - Duration::from_secs(1),
            click_through_enabled: true,
            last_ignore_cursor: false,
            alpha_mask: Vec::new(),
        })))
        .setup(|app| {
            let cfg_dir = app
                .path()
                .app_config_dir()
                .unwrap_or_else(|_| std::env::temp_dir().join("vrm_retro_walk"));
            let cfg_path = cfg_dir.join("settings.json");
            let loaded: SavedSettings = std::fs::read_to_string(&cfg_path)
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_default();

            {
                let layout_state = app.state::<LayoutState>();
                let mut st = layout_state.0.lock().unwrap();
                let h = loaded.win_h.clamp(136.0, 820.0).round();
                let w = (h * ASPECT_RATIO).round();
                st.win_w = w;
                st.win_h = h;
                st.normal_w = w;
                st.normal_h = h;
                st.perch_enabled = loaded.perch_enabled;
                st.intro_enabled = loaded.intro_enabled;
                st.click_through_enabled = loaded.click_through;
            }
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.set_always_on_top(loaded.pinned);
            }

            let initial_vrm = loaded.last_vrm_path.clone();
            // Remove old dropped-avatar copies (including ones refused by the license check),
            // keeping only the avatar that is still in use.
            if let Ok(entries) = std::fs::read_dir(cfg_dir.join("avatars")) {
                let keep = initial_vrm.as_deref().map(std::path::PathBuf::from);
                for entry in entries.flatten() {
                    let p = entry.path();
                    let is_copy = p
                        .file_name()
                        .and_then(|n| n.to_str())
                        .map(|n| n.starts_with("uploaded_"))
                        .unwrap_or(false);
                    if is_copy && keep.as_ref() != Some(&p) {
                        let _ = std::fs::remove_file(&p);
                    }
                }
            }
            app.manage(SettingsState {
                path: cfg_path,
                config_dir: cfg_dir,
                data: Mutex::new(loaded),
            });

            let (child, stdin, stdout) = spawn_python_worker(app.handle(), initial_vrm.as_deref())?;
            {
                let pipe_state = app.state::<PipeState>();
                let mut pipe = pipe_state.0.lock().unwrap();
                pipe.child = Some(child);
                pipe.stdin = Some(stdin);
                pipe.stdout = Some(stdout);
            }

            let show_i = MenuItem::with_id(app, "show", "Show / Focus", true, None::<&str>)?;
            let center_i = MenuItem::with_id(app, "center", "Come to Floor Center", true, None::<&str>)?;
            let hide_i = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_i, &center_i, &hide_i, &quit_i])?;

            if let Some(icon) = app.default_window_icon().cloned() {
                TrayIconBuilder::new()
                    .icon(icon)
                    .menu(&menu)
                    .tooltip("VRM RETRO WALK")
                    .on_menu_event(|app, event| match event.id.as_ref() {
                        "show" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.set_ignore_cursor_events(false);
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                        "center" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let win = w.as_ref().window();
                                let (wa_x, wa_y, wa_w, wa_h, _) = monitor_work_area(&win);
                                let (wx, wy) = {
                                    let state = app.state::<LayoutState>();
                                    let mut st = state.0.lock().unwrap();
                                    st.perch_wid = None;
                                    st.win_x = wa_x + (wa_w - st.win_w) * 0.5;
                                    st.win_y = floor_y_for(wa_y, wa_h, st.win_h);
                                    st.airborne = false;
                                    st.last_ignore_cursor = false;
                                    (st.win_x, st.win_y)
                                };
                                let _ = w.set_ignore_cursor_events(false);
                                let _ = w.set_position(Position::Logical(LogicalPosition::new(wx, wy)));
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                        "hide" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.hide();
                            }
                        }
                        "quit" => {
                            let pipe_state = app.state::<PipeState>();
                            if let Ok(mut pipe) = pipe_state.0.lock() {
                                pipe.kill();
                            };
                            app.exit(0);
                        }
                        _ => {}
                    })
                    .build(app)?;
            }
            Ok(())
        })
        .on_window_event(|window, event| match event {
            WindowEvent::DragDrop(dd) => {
                let drop_state = window.state::<DropState>();
                if let Ok(mut inbox) = drop_state.0.lock() {
                    match dd {
                        DragDropEvent::Enter { .. } | DragDropEvent::Over { .. } => {
                            inbox.drag_hover_file = true;
                        }
                        DragDropEvent::Leave => {
                            inbox.drag_hover_file = false;
                        }
                        DragDropEvent::Drop { paths, .. } => {
                            inbox.drag_hover_file = false;
                            if let Some(p) = paths.iter().find(|p| {
                                p.extension()
                                    .and_then(|e| e.to_str())
                                    .map(|e| {
                                        let l = e.to_ascii_lowercase();
                                        l == "vrm" || l == "glb"
                                    })
                                    .unwrap_or(false)
                            }) {
                                inbox.pending_cmd = Some((
                                    "load_vrm".to_string(),
                                    Some(p.to_string_lossy().into_owned()),
                                ));
                            }
                        }
                        _ => {}
                    }
                };
            }
            WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed => {
                let pipe_state = window.state::<PipeState>();
                if let Ok(mut pipe) = pipe_state.0.lock() {
                    pipe.kill();
                };
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            step_mascot,
            get_saved_settings,
            save_ui_prefs,
            get_window_pos,
            move_mascot_to,
            release_grab,
            set_mascot_height,
            toggle_big_view,
            save_temp_vrm,
            reset_sample_vrm,
            replay_intro,
            set_intro_enabled,
            set_perch_enabled,
            set_click_through,
            drop_to_floor,
            set_pin_top,
            recenter_mascot,
            close_walk,
        ])
        .build(tauri::generate_context!())
        .expect("error building VRM RETRO WALK");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            if let Some(pipe_state) = app_handle.try_state::<PipeState>() {
                if let Ok(mut pipe) = pipe_state.0.lock() {
                    pipe.kill();
                };
            }
        }
    });
}
