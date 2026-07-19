use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

// 缩略图预生成进度
static THUMB_TOTAL: AtomicUsize = AtomicUsize::new(0);
static THUMB_DONE: AtomicUsize = AtomicUsize::new(0);

#[derive(Debug, Serialize)]
pub struct ThumbProgress {
    pub done: usize,
    pub total: usize,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PhotoInfo {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub has_raw_pair: bool,
    /// pending | keep | drop | unsure（断点续传：已在子文件夹的照片恢复状态）
    pub status: String,
}

#[derive(Debug, Serialize)]
pub struct ScanResult {
    pub photos: Vec<PhotoInfo>,
    pub total_count: usize,
    pub scan_ms: u64,
}

#[derive(Debug, Serialize)]
pub struct ThumbResult {
    /// base64 data URL，直接给 <img src=>
    pub data_url: Option<String>,
    pub gen_ms: u64,
}

#[derive(Debug, Serialize)]
pub struct WorkspaceResult {
    pub keep_dir: String,
    pub drop_dir: String,
    pub unsure_dir: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct MoveRecord {
    /// 每个文件: (原路径, 目标路径)
    pub moves: Vec<(String, String)>,
    /// 之前的状态（用于前端恢复 UI）
    pub from_status: String,
    pub to_status: String,
}

#[derive(Debug, Serialize)]
pub struct MoveResult {
    pub moved_files: Vec<String>,
    pub target_dir: String,
    /// 撤销记录，前端存入撤销栈
    pub undo: MoveRecord,
}

const IMAGE_EXTS: &[&str] = &["jpg", "jpeg", "png", "webp", "bmp", "tiff"];
const RAW_EXTS: &[&str] = &["cr3", "nef", "arw", "dng", "raf", "orf", "rw2"];
const VIDEO_EXTS: &[&str] = &["mp4", "mov", "avi", "mkv", "webm", "m4v", "mpg", "mpeg", "wmv", "flv", "3gp"];
const THUMB_SIZE: u32 = 200;

/// 根据媒体类型返回容器文件夹名（图片模式→"图片"，视频模式→"视频"）
fn container_name(kind: &str) -> &'static str {
    match kind {
        "video" => "视频",
        _ => "图片",
    }
}

/// 该类型对应的扩展名集
fn exts_for_kind(kind: &str) -> &'static [&'static str] {
    match kind {
        "video" => VIDEO_EXTS,
        _ => IMAGE_EXTS,
    }
}

fn cache_dir() -> PathBuf {
    let dir = dirs::cache_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("snappick-spike3")
        .join("thumbnails");
    let _ = fs::create_dir_all(&dir);
    dir
}

fn thumb_cache_path(photo_path: &str) -> PathBuf {
    let mut hash: u64 = 5381;
    for b in photo_path.bytes() {
        hash = hash.wrapping_mul(33).wrapping_add(b as u64);
    }
    cache_dir().join(format!("{}.jpg", hash))
}

/// 扫描目录（快速，不需要异步）
/// kind: "photo" | "video"，决定收哪种类型、识别哪个容器
#[tauri::command]
fn scan_photos(dir_path: String, kind: String) -> Result<ScanResult, String> {
    let start = Instant::now();
    let dir = PathBuf::from(&dir_path);

    if !dir.exists() || !dir.is_dir() {
        return Err(format!("目录不存在: {}", dir_path));
    }

    let mut photos = Vec::new();
    let mut seen_stems = std::collections::HashSet::new();
    scan_dir_recursive(&dir, &mut photos, &mut seen_stems, "pending", &kind)?;

    Ok(ScanResult {
        total_count: photos.len(),
        photos,
        scan_ms: start.elapsed().as_millis() as u64,
    })
}

fn scan_dir_recursive(
    dir: &PathBuf,
    photos: &mut Vec<PhotoInfo>,
    seen_stems: &mut std::collections::HashSet<String>,
    status: &str,
    kind: &str,
) -> Result<(), String> {
    let my_container = container_name(kind);
    let other_container = if kind == "video" { "图片" } else { "视频" };
    let target_exts = exts_for_kind(kind);
    let is_video = kind == "video";

    let entries = fs::read_dir(dir).map_err(|e| format!("读取目录失败: {}", e))?;

    for entry in entries {
        let entry = entry.map_err(|e| format!("读取条目失败: {}", e))?;
        let path = entry.path();

        if path.is_dir() {
            let child_status = match path.file_name().and_then(|n| n.to_str()) {
                Some("保留") => "keep",
                Some("删除") => "drop",
                Some("待定") => "unsure",
                // 自己的容器：进入，状态继承（pending，里面再认状态文件夹）
                Some(name) if name == my_container => status,
                // 另一种类型的容器：跳过，避免误收
                Some(name) if name == other_container => continue,
                _ => status,
            };
            scan_dir_recursive(&path, photos, seen_stems, child_status, kind)?;
            continue;
        }

        let ext = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.to_lowercase())
            .unwrap_or_default();

        // 按类型过滤：视频模式收视频扩展名；图片模式收图片扩展名，跳过 RAW（配对跟随）
        if is_video {
            if !VIDEO_EXTS.contains(&ext.as_str()) {
                continue;
            }
        } else {
            if RAW_EXTS.contains(&ext.as_str()) || !IMAGE_EXTS.contains(&ext.as_str()) {
                continue;
            }
        }
        // 兜底
        if !target_exts.contains(&ext.as_str()) && ext != "" {
            // 已被上面分支处理，这里不重复
        }

        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();

        if seen_stems.contains(&stem) {
            continue;
        }
        seen_stems.insert(stem);

        let metadata = fs::metadata(&path).map_err(|e| format!("读取文件信息失败: {}", e))?;
        // 视频无 RAW 配对；图片才检查
        let has_raw_pair = if is_video {
            false
        } else {
            RAW_EXTS.iter().any(|raw_ext| {
                let mut raw_path = path.clone();
                raw_path.set_extension(raw_ext);
                raw_path.exists()
            })
        };

        photos.push(PhotoInfo {
            name: path.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string(),
            path: path.to_str().unwrap_or("").to_string(),
            size_bytes: metadata.len(),
            has_raw_pair,
            status: status.to_string(),
        });
    }
    Ok(())
}

/// 异步获取单张缩略图——不阻塞主线程
/// kind: photo | video，视频走 ffmpeg 抽首帧
#[tauri::command]
async fn get_thumbnail(photo_path: String, kind: String) -> Result<ThumbResult, String> {
    let result = tauri::async_runtime::spawn_blocking(move || {
        if kind == "video" {
            generate_video_thumbnail(&photo_path)
        } else {
            generate_thumbnail_sync(&photo_path)
        }
    })
    .await
    .map_err(|e| format!("任务执行失败: {}", e))?;

    result
}

/// 检测系统是否安装 ffmpeg（视频抽帧依赖）
#[tauri::command]
fn detect_ffmpeg() -> bool {
    std::process::Command::new("ffmpeg")
        .arg("-version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok()
}

fn generate_video_thumbnail(video_path: &str) -> Result<ThumbResult, String> {
    let start = Instant::now();
    let cache_path = thumb_cache_path(video_path);

    // 缓存命中
    if cache_path.exists() {
        let data = fs::read(&cache_path).map_err(|e| format!("读缓存失败: {}", e))?;
        let b64 = base64_encode(&data);
        return Ok(ThumbResult {
            data_url: Some(format!("data:image/jpeg;base64,{}", b64)),
            gen_ms: 0,
        });
    }

    // 检查 ffmpeg
    let probe = std::process::Command::new("ffmpeg")
        .arg("-version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    if probe.is_err() {
        // 无 ffmpeg：返回空标记，前端显示文件名卡片
        return Ok(ThumbResult {
            data_url: None,
            gen_ms: 0,
        });
    }

    // 调 ffmpeg 抽首帧：-ss 1 跳过 1 秒避免黑屏，缩放到 200 宽
    let tmp_out = cache_path.with_extension("tmp.jpg");
    let status = std::process::Command::new("ffmpeg")
        .arg("-y")
        .arg("-i").arg(video_path)
        .arg("-ss").arg("1")
        .arg("-frames:v").arg("1")
        .arg("-vf").arg(format!("scale={}:{}", THUMB_SIZE, THUMB_SIZE))
        .arg("-q:v").arg("3")
        .arg(&tmp_out)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map_err(|e| format!("ffmpeg 执行失败: {}", e))?;

    if !status.success() || !tmp_out.exists() {
        return Ok(ThumbResult { data_url: None, gen_ms: 0 });
    }

    // 重命名为缓存文件
    let _ = fs::rename(&tmp_out, &cache_path);
    let data = fs::read(&cache_path).map_err(|e| format!("读抽帧失败: {}", e))?;
    let b64 = base64_encode(&data);

    Ok(ThumbResult {
        data_url: Some(format!("data:image/jpeg;base64,{}", b64)),
        gen_ms: start.elapsed().as_millis() as u64,
    })
}

fn generate_thumbnail_sync(photo_path: &str) -> Result<ThumbResult, String> {
    let start = Instant::now();
    let cache_path = thumb_cache_path(photo_path);

    // 缓存命中：读文件返回 base64
    if cache_path.exists() {
        let data = fs::read(&cache_path).map_err(|e| format!("读缓存失败: {}", e))?;
        let b64 = base64_encode(&data);
        return Ok(ThumbResult {
            data_url: Some(format!("data:image/jpeg;base64,{}", b64)),
            gen_ms: start.elapsed().as_millis() as u64,
        });
    }

    // 缓存未命中：生成缩略图
    let src_path = PathBuf::from(photo_path);
    let img = image::open(&src_path).map_err(|e| format!("打开图片失败: {}", e))?;
    let thumb = img.thumbnail(THUMB_SIZE, THUMB_SIZE);

    let mut buf = Vec::new();
    use image::ImageEncoder;
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, 75)
        .write_image(thumb.as_bytes(), thumb.width(), thumb.height(), thumb.color().into())
        .map_err(|e| format!("编码 JPEG 失败: {}", e))?;

    // 写缓存（忽略失败）
    let _ = fs::write(&cache_path, &buf);

    let b64 = base64_encode(&buf);
    Ok(ThumbResult {
        data_url: Some(format!("data:image/jpeg;base64,{}", b64)),
        gen_ms: start.elapsed().as_millis() as u64,
    })
}

/// 创建工作区子文件夹（图片模式：图片/{保留,删除,待定}；视频模式：视频/{保留,删除,待定}）
#[tauri::command]
fn prepare_workspace(dir_path: String, kind: String) -> Result<WorkspaceResult, String> {
    let root = PathBuf::from(&dir_path);
    if !root.exists() || !root.is_dir() {
        return Err(format!("目录不存在: {}", dir_path));
    }

    let container = root.join(container_name(&kind));
    fs::create_dir_all(&container).map_err(|e| format!("创建容器文件夹失败: {}", e))?;

    let keep_dir = container.join("保留");
    let drop_dir = container.join("删除");
    let unsure_dir = container.join("待定");

    fs::create_dir_all(&keep_dir).map_err(|e| format!("创建保留文件夹失败: {}", e))?;
    fs::create_dir_all(&drop_dir).map_err(|e| format!("创建删除文件夹失败: {}", e))?;
    fs::create_dir_all(&unsure_dir).map_err(|e| format!("创建待定文件夹失败: {}", e))?;

    Ok(WorkspaceResult {
        keep_dir: keep_dir.to_string_lossy().to_string(),
        drop_dir: drop_dir.to_string_lossy().to_string(),
        unsure_dir: unsure_dir.to_string_lossy().to_string(),
    })
}

/// 将一张照片/视频及其 RAW/XMP 配对移动到目标分类文件夹。
/// target: keep | drop | unsure
/// kind: photo | video（决定容器名 图片/视频）
#[tauri::command]
fn move_photo(photo_path: String, target: String, kind: String) -> Result<MoveResult, String> {
    let source = PathBuf::from(&photo_path);
    if !source.exists() {
        return Err(format!("源文件不存在: {}", photo_path));
    }

    // 推断工作区根目录：路径里找到 "图片" 或 "视频" 容器，其上一级是根目录；
    // 都没有就视源文件父目录为根目录。
    let root = find_workspace_root(&source);
    let container = root.join(container_name(&kind));

    let target_name = match target.as_str() {
        "keep" => "保留",
        "drop" => "删除",
        "unsure" => "待定",
        _ => return Err(format!("未知目标状态: {}", target)),
    };
    let target_dir = container.join(target_name);
    fs::create_dir_all(&target_dir).map_err(|e| format!("创建目标文件夹失败: {}", e))?;

    let pair_files = collect_pair_files(&source);
    if pair_files.is_empty() {
        return Err("未找到可移动文件".to_string());
    }

    // 预检查：所有源文件存在，目标文件不存在。
    for src in &pair_files {
        if !src.exists() {
            return Err(format!("配对源文件不存在: {}", src.to_string_lossy()));
        }
        let dest = target_dir.join(src.file_name().unwrap());
        if dest.exists() {
            return Err(format!("目标已存在同名文件: {}", dest.to_string_lossy()));
        }
    }

    // 顺序移动；失败则回滚已移动项。
    let mut moved: Vec<(PathBuf, PathBuf)> = Vec::new();
    for src in &pair_files {
        let dest = target_dir.join(src.file_name().unwrap());
        match fs::rename(src, &dest) {
            Ok(()) => moved.push((src.clone(), dest)),
            Err(e) => {
                for (orig, moved_to) in moved.iter().rev() {
                    let _ = fs::rename(moved_to, orig);
                }
                return Err(format!("移动失败，已尝试回滚: {}", e));
            }
        }
    }

    let undo_moves: Vec<(String, String)> = moved
        .iter()
        .map(|(orig, dest)| (orig.to_string_lossy().to_string(), dest.to_string_lossy().to_string()))
        .collect();

    let moved_files: Vec<String> = moved
        .iter()
        .map(|(_, dest)| dest.to_string_lossy().to_string())
        .collect();

    Ok(MoveResult {
        target_dir: target_dir.to_string_lossy().to_string(),
        moved_files,
        undo: MoveRecord {
            moves: undo_moves,
            from_status: detect_status(&source).to_string(),
            to_status: target.clone(),
        },
    })
}

/// 从文件路径推断工作区根目录：
/// 找路径里出现的 "图片" 或 "视频" 容器段，返回它的父目录；找不到则返回文件所在目录。
fn find_workspace_root(path: &PathBuf) -> PathBuf {
    let ancestors = path.ancestors();
    let mut found: Option<PathBuf> = None;
    for anc in ancestors {
        if let Some(name) = anc.file_name().and_then(|n| n.to_str()) {
            if name == "图片" || name == "视频" {
                if let Some(parent) = anc.parent() {
                    found = Some(parent.to_path_buf());
                    break;
                }
            }
        }
    }
    found.unwrap_or_else(|| path.parent().unwrap_or(path).to_path_buf())
}

/// 从文件路径推断当前状态（用于撤销记录的 from_status）
fn detect_status(path: &PathBuf) -> &'static str {
    for anc in path.ancestors() {
        if let Some(name) = anc.file_name().and_then(|n| n.to_str()) {
            match name {
                "保留" => return "keep",
                "删除" => return "drop",
                "待定" => return "unsure",
                _ => {}
            }
        }
    }
    "pending"
}

/// 撤销上一次移动：把每个文件从 目标路径 移回 原路径
#[tauri::command]
fn undo_move(record: MoveRecord) -> Result<String, String> {
    if record.moves.is_empty() {
        return Err("撤销记录为空".to_string());
    }
    let mut undone = 0;
    let mut last_err = String::new();
    // 反向移动：从 to 移回 from
    for (from, to) in record.moves.iter().rev() {
        let to_path = PathBuf::from(to);
        let from_path = PathBuf::from(from);
        if !to_path.exists() {
            last_err = format!("目标文件已不存在: {}", to);
            continue;
        }
        // 确保 from 的父目录存在
        if let Some(parent) = from_path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        match fs::rename(&to_path, &from_path) {
            Ok(()) => undone += 1,
            Err(e) => last_err = format!("撤销失败 {}: {}", to, e),
        }
    }
    if undone == 0 {
        return Err(format!("无法撤销: {}", last_err));
    }
    Ok(format!("已撤销 {} 个文件", undone))
}

/// 用系统默认图片查看器打开照片
#[tauri::command]
fn open_photo(photo_path: String) -> Result<(), String> {
    let path = PathBuf::from(&photo_path);
    if !path.exists() {
        return Err(format!("文件不存在: {}", photo_path));
    }
    open::that(&path).map_err(|e| format!("打开系统查看器失败: {}", e))
}

/// 后台批量预生成所有缩略图（不阻塞 IPC 线程）
#[tauri::command]
fn prefetch_thumbnails(photos: Vec<PhotoInfo>) -> Result<(), String> {
    THUMB_TOTAL.store(photos.len(), Ordering::SeqCst);
    THUMB_DONE.store(0, Ordering::SeqCst);

    let photos: Vec<String> = photos.into_iter().map(|p| p.path).collect();

    std::thread::spawn(move || {
        use rayon::prelude::*;
        photos.par_iter().for_each(|path| {
            let _ = generate_thumbnail_sync(path);
            THUMB_DONE.fetch_add(1, Ordering::SeqCst);
        });
    });

    Ok(())
}

/// 查询缩略图预生成进度
#[tauri::command]
fn get_thumb_progress() -> ThumbProgress {
    ThumbProgress {
        done: THUMB_DONE.load(Ordering::SeqCst),
        total: THUMB_TOTAL.load(Ordering::SeqCst),
    }
}

/// 获取大图预览（最长边 1920px JPEG base64）
#[tauri::command]
async fn get_preview(photo_path: String) -> Result<ThumbResult, String> {
    let result = tauri::async_runtime::spawn_blocking(move || {
        generate_preview_sync(&photo_path)
    })
    .await
    .map_err(|e| format!("任务执行失败: {}", e))?;

    result
}

/// 预览缓存路径
fn preview_cache_path(photo_path: &str) -> PathBuf {
    let mut hash: u64 = 5381;
    for b in photo_path.bytes() {
        hash = hash.wrapping_mul(33).wrapping_add(b as u64);
    }
    let dir = dirs::cache_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("snappick-spike3")
        .join("previews");
    let _ = fs::create_dir_all(&dir);
    dir.join(format!("{}.jpg", hash))
}

/// 生成预览图：最长边 1920px
fn generate_preview_sync(photo_path: &str) -> Result<ThumbResult, String> {
    use image::imageops::FilterType;
    let start = Instant::now();
    let cache_path = preview_cache_path(photo_path);

    if cache_path.exists() {
        let data = fs::read(&cache_path).map_err(|e| format!("读预览缓存失败: {}", e))?;
        let b64 = base64_encode(&data);
        return Ok(ThumbResult {
            data_url: Some(format!("data:image/jpeg;base64,{}", b64)),
            gen_ms: 0,
        });
    }

    let src_path = PathBuf::from(photo_path);
    let img = image::open(&src_path).map_err(|e| format!("打开图片失败: {}", e))?;

    let preview = if img.width() > 1920 || img.height() > 1920 {
        if img.width() > img.height() {
            img.resize(1920, 0, FilterType::Lanczos3)
        } else {
            img.resize(0, 1920, FilterType::Lanczos3)
        }
    } else {
        img
    };

    let mut buf = Vec::new();
    use image::ImageEncoder;
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, 85)
        .write_image(preview.as_bytes(), preview.width(), preview.height(), preview.color().into())
        .map_err(|e| format!("编码 JPEG 失败: {}", e))?;

    let _ = fs::write(&cache_path, &buf);

    let b64 = base64_encode(&buf);
    Ok(ThumbResult {
        data_url: Some(format!("data:image/jpeg;base64,{}", b64)),
        gen_ms: start.elapsed().as_millis() as u64,
    })
}

fn collect_pair_files(source: &PathBuf) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let Some(parent) = source.parent() else { return files; };
    let Some(stem) = source.file_stem().and_then(|s| s.to_str()) else { return files; };

    // 直接读取目录里所有同 stem 的文件，避免大小写双重收集导致的 os error 2。
    // 这样也顺带支持任意扩展名配对（CR3/NEF/ARW/XMP/JPG 等，不管大小写）。
    let entries = match fs::read_dir(parent) {
        Ok(e) => e,
        Err(_) => return files,
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        if let Some(s) = path.file_stem().and_then(|x| x.to_str()) {
            if s == stem {
                files.push(path);
            }
        }
    }

    files
}

fn base64_encode(data: &[u8]) -> String {
    const CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut result = String::with_capacity(data.len() * 4 / 3 + 4);
    let mut i = 0;
    while i + 2 < data.len() {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8) | (data[i + 2] as u32);
        result.push(CHARS[((n >> 18) & 0x3F) as usize] as char);
        result.push(CHARS[((n >> 12) & 0x3F) as usize] as char);
        result.push(CHARS[((n >> 6) & 0x3F) as usize] as char);
        result.push(CHARS[(n & 0x3F) as usize] as char);
        i += 3;
    }
    if data.len() % 3 == 1 {
        let n = (data[i] as u32) << 16;
        result.push(CHARS[((n >> 18) & 0x3F) as usize] as char);
        result.push(CHARS[((n >> 12) & 0x3F) as usize] as char);
        result.push('=');
        result.push('=');
    } else if data.len() % 3 == 2 {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8);
        result.push(CHARS[((n >> 18) & 0x3F) as usize] as char);
        result.push(CHARS[((n >> 12) & 0x3F) as usize] as char);
        result.push(CHARS[((n >> 6) & 0x3F) as usize] as char);
        result.push('=');
    }
    result
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            scan_photos,
            get_thumbnail,
            get_preview,
            detect_ffmpeg,
            prefetch_thumbnails,
            get_thumb_progress,
            prepare_workspace,
            move_photo,
            undo_move,
            open_photo
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
