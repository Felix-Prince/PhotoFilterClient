use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PhotoInfo {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub has_raw_pair: bool,
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

const IMAGE_EXTS: &[&str] = &["jpg", "jpeg", "png", "webp", "bmp", "tiff"];
const RAW_EXTS: &[&str] = &["cr3", "nef", "arw", "dng", "raf", "orf", "rw2"];
const THUMB_SIZE: u32 = 200;

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
#[tauri::command]
fn scan_photos(dir_path: String) -> Result<ScanResult, String> {
    let start = Instant::now();
    let dir = PathBuf::from(&dir_path);

    if !dir.exists() || !dir.is_dir() {
        return Err(format!("目录不存在: {}", dir_path));
    }

    let mut photos = Vec::new();
    let mut seen_stems = std::collections::HashSet::new();
    scan_dir_recursive(&dir, &mut photos, &mut seen_stems)?;

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
) -> Result<(), String> {
    let entries = fs::read_dir(dir).map_err(|e| format!("读取目录失败: {}", e))?;

    for entry in entries {
        let entry = entry.map_err(|e| format!("读取条目失败: {}", e))?;
        let path = entry.path();

        if path.is_dir() {
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                if ["保留", "删除", "待定"].contains(&name) {
                    continue;
                }
            }
            scan_dir_recursive(&path, photos, seen_stems)?;
            continue;
        }

        let ext = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.to_lowercase())
            .unwrap_or_default();

        if RAW_EXTS.contains(&ext.as_str()) || !IMAGE_EXTS.contains(&ext.as_str()) {
            continue;
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
        let has_raw_pair = RAW_EXTS.iter().any(|raw_ext| {
            let mut raw_path = path.clone();
            raw_path.set_extension(raw_ext);
            raw_path.exists()
        });

        photos.push(PhotoInfo {
            name: path.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string(),
            path: path.to_str().unwrap_or("").to_string(),
            size_bytes: metadata.len(),
            has_raw_pair,
        });
    }
    Ok(())
}

/// 异步获取单张缩略图——不阻塞主线程
#[tauri::command]
async fn get_thumbnail(photo_path: String) -> Result<ThumbResult, String> {
    // 把 CPU 密集的图片处理放到单独线程
    let result = tauri::async_runtime::spawn_blocking(move || {
        generate_thumbnail_sync(&photo_path)
    })
    .await
    .map_err(|e| format!("任务执行失败: {}", e))?;

    result
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
        .invoke_handler(tauri::generate_handler![scan_photos, get_thumbnail])
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
