<template>
  <div class="app" tabindex="0" @keydown="onKeydown">
    <header>
      <h1>📷 SnapPick — 阶段一手动快筛</h1>
      <div class="controls">
        <button @click="selectFolder" :disabled="loading">选择照片目录</button>
        <button @click="prepare" :disabled="!folder || loading">准备工作区</button>
        <button @click="clearResults">清空</button>
        <span v-if="folder" class="folder-path">📁 {{ folder }}</span>
        <span v-if="kind" class="kind-badge">{{ kind === 'video' ? '🎬 视频' : '🖼 图片' }}</span>
        <span v-if="kind === 'video' && !ffmpegAvailable" class="warn-badge" title="未检测到 ffmpeg，视频仅显示文件名">⚠ 无 ffmpeg</span>
      </div>
    </header>

    <div class="stats" v-if="photos.length > 0">
      <div class="stat"><span class="label">总数</span><span class="value">{{ photos.length }}</span></div>
      <div class="stat pending-stat"><span class="label">待处理</span><span class="value">{{ pendingCount }}</span></div>
      <div class="stat keep-stat"><span class="label">保留</span><span class="value">{{ countByStatus.keep }}</span></div>
      <div class="stat unsure-stat"><span class="label">待定</span><span class="value">{{ countByStatus.unsure }}</span></div>
      <div class="stat drop-stat"><span class="label">删除</span><span class="value">{{ countByStatus.drop }}</span></div>
      <div class="stat"><span class="label">进度</span><span class="value">{{ progressPct }}%</span></div>
      <div class="stat" v-if="thumbTotal > 0 && thumbDone < thumbTotal">
        <span class="label">缩略图</span>
        <span class="value">{{ thumbDone }}/{{ thumbTotal }}</span>
      </div>
      <div class="stat"><span class="label">扫描</span><span class="value">{{ scanMs ?? '—' }}ms</span></div>
    </div>

    <!-- 当前选中照片的操作栏：永远显示当前焦点 -->
    <div v-if="selectedPhoto" class="action-bar" :class="selectedPhoto.status">
      <span class="current-badge">{{ statusLabel(selectedPhoto.status) }}</span>
      <strong>{{ selectedPhoto.name }}</strong>
      <span class="current-status-label" v-if="selectedPhoto.status !== 'pending'">
        已分类为「{{ statusText(selectedPhoto.status) }}」
      </span>
      <div class="action-buttons">
        <button @click="classifySelected('keep')" :class="{ active: selectedPhoto.status === 'keep' }">P 保留</button>
        <button @click="classifySelected('drop')" :class="{ active: selectedPhoto.status === 'drop' }">X 删除</button>
        <button @click="classifySelected('unsure')" :class="{ active: selectedPhoto.status === 'unsure' }">U 待定</button>
        <button @click="openSystemViewer" class="preview-btn" title="用系统查看器打开大图 (Enter)">🔍 预览</button>
        <button
          v-if="undoStack.length > 0"
          @click="undo"
          class="undo-btn"
          title="撤销上一步 (Ctrl+Z)"
        >↶ 撤销 ({{ undoStack.length }})</button>
      </div>
      <span class="hint">←/→ 选择 · P/X/U 分类 · Enter 系统预览 · 双击卡片 · Ctrl+Z 撤销</span>
    </div>

    <div v-if="loading" class="loading">{{ loadingMsg }}</div>
    <div v-if="error" class="error">❌ {{ error }}</div>
    <div v-if="message" class="message">{{ message }}</div>

    <!-- 类型选择弹窗：选目录后出现 -->
    <div v-if="showKindPicker" class="modal-overlay" @click.self="cancelKindPicker">
      <div class="modal">
        <h3>选择筛选类型</h3>
        <p class="modal-desc">{{ folder }}</p>
        <div class="kind-options">
          <button class="kind-option" @click="confirmKind('photo')">
            <span class="kind-icon">🖼</span>
            <span class="kind-label">图片筛选</span>
            <span class="kind-sub">保留 / 删除 / 待定</span>
          </button>
          <button class="kind-option" @click="confirmKind('video')">
            <span class="kind-icon">🎬</span>
            <span class="kind-label">视频筛选</span>
            <span class="kind-sub">保留 / 删除 / 待定</span>
          </button>
        </div>
        <button class="modal-cancel" @click="cancelKindPicker">取消</button>
      </div>
    </div>

    <!-- 待处理区（主区域，最大） -->
    <section v-if="photos.length > 0" class="zone pending-zone">
      <div class="zone-header">
        <span class="zone-title">📋 待处理</span>
        <span class="zone-count">{{ pendingCount }}</span>
        <span class="zone-hint" v-if="pendingCount === 0">✅ 全部已分类</span>
      </div>
      <div class="grid pending-grid" ref="pendingGridRef">
        <div
          v-for="photo in pendingPhotos"
          :key="photo.name"
          class="card"
          :class="{ selected: photo._idx === selectedIndex }"
          :data-idx="photo._idx"
          @click="selectPhoto(photo._idx)"
          @dblclick="openPreviewIdx(photo._idx)"
        >
          <img v-if="photo.dataUrl" :src="photo.dataUrl" :alt="photo.name" @load="onLoad(photo._idx)" />
          <div v-else-if="photo.loading" class="placeholder dot">...</div>
          <div v-else-if="kind === 'video'" class="placeholder video-ph">
            <span class="video-icon">🎬</span>
            <span class="video-size">{{ fileSizeText(photo.size_bytes) }}</span>
          </div>
          <div v-else class="placeholder">⏳</div>
          <div class="card-name">{{ photo.name }}</div>
        </div>
        <div v-if="pendingCount === 0" class="empty-hint">没有待处理项</div>
      </div>
    </section>

    <!-- 下方三分类区 -->
    <div v-if="photos.length > 0" class="classified-row">
      <section class="zone keep-zone">
        <div class="zone-header">
          <span class="zone-dot keep-dot"></span>
          <span class="zone-title">保留</span>
          <span class="zone-count">{{ countByStatus.keep }}</span>
        </div>
        <div class="classified-scroll">
          <div class="grid classified-grid">
            <div
              v-for="photo in keepPhotos"
              :key="photo.name"
              class="card keep"
              :class="{ selected: photo._idx === selectedIndex }"
              :data-idx="photo._idx"
              @click="selectPhoto(photo._idx)"
              @dblclick="openPreviewIdx(photo._idx)"
            >
              <img v-if="photo.dataUrl" :src="photo.dataUrl" :alt="photo.name" />
              <div v-else-if="kind === 'video'" class="placeholder video-ph">
                <span class="video-icon">🎬</span>
                <span class="video-size">{{ fileSizeText(photo.size_bytes) }}</span>
              </div>
              <div v-else class="placeholder">⏳</div>
              <div class="card-name">{{ photo.name }}</div>
            </div>
            <div v-if="countByStatus.keep === 0" class="empty-hint">空</div>
          </div>
        </div>
      </section>

      <section class="zone unsure-zone">
        <div class="zone-header">
          <span class="zone-dot unsure-dot"></span>
          <span class="zone-title">待定</span>
          <span class="zone-count">{{ countByStatus.unsure }}</span>
        </div>
        <div class="classified-scroll">
          <div class="grid classified-grid">
            <div
              v-for="photo in unsurePhotos"
              :key="photo.name"
              class="card unsure"
              :class="{ selected: photo._idx === selectedIndex }"
              :data-idx="photo._idx"
              @click="selectPhoto(photo._idx)"
              @dblclick="openPreviewIdx(photo._idx)"
            >
              <img v-if="photo.dataUrl" :src="photo.dataUrl" :alt="photo.name" />
              <div v-else-if="kind === 'video'" class="placeholder video-ph">
                <span class="video-icon">🎬</span>
                <span class="video-size">{{ fileSizeText(photo.size_bytes) }}</span>
              </div>
              <div v-else class="placeholder">⏳</div>
              <div class="card-name">{{ photo.name }}</div>
            </div>
            <div v-if="countByStatus.unsure === 0" class="empty-hint">空</div>
          </div>
        </div>
      </section>

      <section class="zone drop-zone">
        <div class="zone-header">
          <span class="zone-dot drop-dot"></span>
          <span class="zone-title">删除</span>
          <span class="zone-count">{{ countByStatus.drop }}</span>
        </div>
        <div class="classified-scroll">
          <div class="grid classified-grid">
            <div
              v-for="photo in dropPhotos"
              :key="photo.name"
              class="card drop"
              :class="{ selected: photo._idx === selectedIndex }"
              :data-idx="photo._idx"
              @click="selectPhoto(photo._idx)"
              @dblclick="openPreviewIdx(photo._idx)"
            >
              <img v-if="photo.dataUrl" :src="photo.dataUrl" :alt="photo.name" />
              <div v-else-if="kind === 'video'" class="placeholder video-ph">
                <span class="video-icon">🎬</span>
                <span class="video-size">{{ fileSizeText(photo.size_bytes) }}</span>
              </div>
              <div v-else class="placeholder">⏳</div>
              <div class="card-name">{{ photo.name }}</div>
            </div>
            <div v-if="countByStatus.drop === 0" class="empty-hint">空</div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, nextTick, onBeforeUnmount } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

const photos = ref([]);
const loading = ref(false);
const loadingMsg = ref("");
const folder = ref("");
const error = ref("");
const message = ref("");
const scanMs = ref(null);
const firstScreenMs = ref(null);
const loadedCount = ref(0);
const selectedIndex = ref(0);
const pendingGridRef = ref(null);

// 媒体类型：photo | video
const kind = ref("photo");
const showKindPicker = ref(false);
const ffmpegAvailable = ref(false);

// 缩略图预生成进度
const thumbDone = ref(0);
const thumbTotal = ref(0);
let pollTimer = null;

let t0 = 0;
let loaded = new Set();
let observer = null;
const pendingThumbQueue = new Set();
let activeThumbLoads = 0;
const MAX_CONCURRENT = 12;

// 撤销栈：每项是后端返回的 MoveRecord
const undoStack = ref([]);
const UNDO_MAX = 50;

// 给每张照片打上原始索引，便于在分类视图中定位
const photosWithIdx = computed(() =>
  photos.value.map((p, i) => ({ ...p, _idx: i }))
);
const pendingPhotos = computed(() => photosWithIdx.value.filter(p => p.status === "pending"));
const keepPhotos = computed(() => photosWithIdx.value.filter(p => p.status === "keep"));
const unsurePhotos = computed(() => photosWithIdx.value.filter(p => p.status === "unsure"));
const dropPhotos = computed(() => photosWithIdx.value.filter(p => p.status === "drop"));

const selectedPhoto = computed(() => photos.value[selectedIndex.value]);
const countByStatus = computed(() => {
  const counts = { keep: 0, drop: 0, unsure: 0 };
  for (const p of photos.value) {
    if (p.status in counts) counts[p.status]++;
  }
  return counts;
});
const pendingCount = computed(() => photos.value.filter(p => p.status === "pending").length);
const progressPct = computed(() => {
  if (photos.value.length === 0) return 0;
  const decided = photos.value.length - pendingCount.value;
  return Math.round((decided / photos.value.length) * 100);
});

async function selectFolder() {
  const selected = await open({ directory: true, multiple: false });
  if (!selected) return;

  folder.value = selected;
  showKindPicker.value = true;
}

async function confirmKind(k) {
  kind.value = k;
  showKindPicker.value = false;

  // 视频模式检测 ffmpeg
  if (k === "video") {
    ffmpegAvailable.value = await invoke("detect_ffmpeg");
    if (!ffmpegAvailable.value) {
      message.value = "⚠ 未检测到 ffmpeg，视频将仅显示文件名（无法抽帧缩略图）。双击可用系统播放器预览。";
    }
  } else {
    ffmpegAvailable.value = true;
    message.value = "";
  }

  await startScan();
}

function cancelKindPicker() {
  showKindPicker.value = false;
  folder.value = "";
}

async function startScan() {
  loading.value = true;
  error.value = "";
  message.value = message.value; // 保留 ffmpeg 提示
  photos.value = [];
  selectedIndex.value = 0;
  loadedCount.value = 0;
  loaded.clear();
  pendingThumbQueue.clear();
  activeThumbLoads = 0;
  firstScreenMs.value = null;
  scanMs.value = null;

  t0 = performance.now();
  loadingMsg.value = "扫描中...";

  try {
    const r = await invoke("scan_photos", { dirPath: folder.value, kind: kind.value });
    scanMs.value = r.scan_ms;
    photos.value = r.photos.map(p => ({
      ...p,
      dataUrl: null,
      previewUrl: null,
      loading: false,
      status: p.status || "pending",
    }));
    const firstPending = photos.value.findIndex(p => p.status === "pending");
    selectedIndex.value = firstPending >= 0 ? firstPending : 0;
    undoStack.value = [];
    loading.value = false;

    // 后台预生成缩略图
    thumbDone.value = 0;
    thumbTotal.value = r.photos.length;
    invoke("prefetch_thumbnails", { photos: r.photos });
    startThumbPolling();

    await nextTick();
    startObserver();
  } catch (e) {
    error.value = String(e);
    loading.value = false;
  }
}

async function prepare() {
  if (!folder.value) return;
  try {
    await invoke("prepare_workspace", { dirPath: folder.value, kind: kind.value });
    const cname = kind.value === "video" ? "视频" : "图片";
    message.value = `工作区已准备：${cname}/保留 · ${cname}/删除 · ${cname}/待定`;
  } catch (e) {
    error.value = String(e);
  }
}

function selectPhoto(idx) {
  selectedIndex.value = idx;
}

async function classifySelected(target) {
  const photo = selectedPhoto.value;
  if (!photo) return;
  // 目标相同时不重复移动
  if (photo.status === target) return;

  const prevStatus = photo.status;
  const prevPath = photo.path;

  try {
    const r = await invoke("move_photo", { photoPath: prevPath, target, kind: kind.value });
    // 更新照片路径（文件已移到子文件夹）和状态
    photo.path = r.moved_files.find(f => f.replace(/^.*[\\/]/, "").toLowerCase() === prevPath.replace(/^.*[\\/]/, "").toLowerCase())
      || r.moved_files[0]
      || prevPath;
    photo.status = target;
    undoStack.value.push(r.undo);
    if (undoStack.value.length > UNDO_MAX) undoStack.value.shift();
    if (pendingCount.value > 0) moveToNextPending();
  } catch (e) {
    error.value = String(e);
  }
}

async function undo() {
  const record = undoStack.value.pop();
  if (!record) return;
  try {
    await invoke("undo_move", { record });
    // 找到被撤销的照片（用 undo.moves 里第一个目标路径反推）
    const lastTo = record.moves[0]?.[1];
    if (lastTo) {
      const idx = photos.value.findIndex(p => p.path === lastTo);
      if (idx >= 0) {
        // 恢复路径和状态
        photos.value[idx].path = record.moves[0][0];
        photos.value[idx].status = record.from_status;
        selectedIndex.value = idx;
      }
    }
  } catch (e) {
    error.value = String(e);
    // 撤销失败，把记录放回去
    undoStack.value.push(record);
  }
}

// 用系统默认查看器打开大图
async function openPreviewIdx(idx) {
  const photo = photos.value[idx];
  if (!photo) return;
  try {
    await invoke("open_photo", { photoPath: photo.path });
  } catch (e) {
    error.value = String(e);
  }
}

async function openPreview() {
  if (selectedPhoto.value) await openPreviewIdx(selectedIndex.value);
}

async function openSystemViewer() {
  const photo = selectedPhoto.value;
  if (!photo) return;
  try { await invoke("open_photo", { photoPath: photo.path }); }
  catch (e) { error.value = String(e); }
}

// 缩略图预生成进度轮询
function startThumbPolling() {
  stopThumbPolling();
  pollTimer = setInterval(async () => {
    try {
      const p = await invoke("get_thumb_progress");
      thumbDone.value = p.done;
      thumbTotal.value = p.total;
      if (p.done >= p.total) stopThumbPolling();
    } catch (_) {}
  }, 1000);
}
function stopThumbPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function moveToNextPending() {
  const next = photos.value.findIndex((p, i) => i > selectedIndex.value && p.status === "pending");
  if (next >= 0) {
    selectedIndex.value = next;
    return;
  }
  const first = photos.value.findIndex(p => p.status === "pending");
  if (first >= 0) selectedIndex.value = first;
}

function onKeydown(e) {
  if (e.target?.tagName === "INPUT" || e.target?.tagName === "TEXTAREA") return;
  if (e.key === "ArrowRight" || e.key === " ") {
    e.preventDefault();
    selectedIndex.value = Math.min(photos.value.length - 1, selectedIndex.value + 1);
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    selectedIndex.value = Math.max(0, selectedIndex.value - 1);
  } else if (e.key === "Tab") {
    e.preventDefault();
    moveToNextPending();
  } else if (e.key === "Enter") {
    e.preventDefault();
    openPreview();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    undo();
  } else if (e.key.toLowerCase() === "p") {
    classifySelected("keep");
  } else if (e.key.toLowerCase() === "x") {
    classifySelected("drop");
  } else if (e.key.toLowerCase() === "u") {
    classifySelected("unsure");
  }
}

function startObserver() {
  if (observer) observer.disconnect();
  observer = new IntersectionObserver(entries => {
    for (const e of entries) {
      if (e.isIntersecting) {
        const i = parseInt(e.target.dataset.idx);
        if (!isNaN(i)) scheduleThumb(i);
      }
    }
  }, { rootMargin: "200px", threshold: 0 });

  // observe 所有区域的卡片（待处理 + 三分类）
  document.querySelectorAll(".card[data-idx]").forEach(c => observer.observe(c));
}

function scheduleThumb(i) {
  const p = photos.value[i];
  if (!p || p.dataUrl || p.loading || pendingThumbQueue.has(i)) return;
  if (activeThumbLoads < MAX_CONCURRENT) loadThumb(i);
  else pendingThumbQueue.add(i);
}

function nextThumb() {
  while (activeThumbLoads < MAX_CONCURRENT && pendingThumbQueue.size > 0) {
    const i = pendingThumbQueue.values().next().value;
    pendingThumbQueue.delete(i);
    const p = photos.value[i];
    if (p && !p.dataUrl && !p.loading) loadThumb(i);
  }
}

async function loadThumb(i) {
  const p = photos.value[i];
  if (!p || p.dataUrl || p.loading) return;
  activeThumbLoads++;
  p.loading = true;
  try {
    const r = await invoke("get_thumbnail", { photoPath: p.path, kind: kind.value });
    if (r.data_url) p.dataUrl = r.data_url;
  } catch (e) {
    console.error("thumb err:", i, e);
  } finally {
    p.loading = false;
    activeThumbLoads--;
    nextThumb();
  }
}

function onLoad(i) {
  if (loaded.has(i)) return;
  loaded.add(i);
  loadedCount.value = loaded.size;
  if (loaded.size === Math.min(20, photos.value.length) && firstScreenMs.value == null) {
    firstScreenMs.value = Math.round(performance.now() - t0);
  }
}

function statusLabel(status) {
  if (status === "keep") return kind.value === "video" ? "▶" : "✓";
  if (status === "drop") return "✗";
  if (status === "unsure") return "?";
  return "";
}

function statusText(status) {
  if (status === "keep") return "保留";
  if (status === "drop") return "删除";
  if (status === "unsure") return "待定";
  return "";
}

function fileSizeText(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function clearResults() {
  if (observer) observer.disconnect();
  stopThumbPolling();
  photos.value = [];
  selectedIndex.value = 0;
  loadedCount.value = 0;
  loaded.clear();
  pendingThumbQueue.clear();
  activeThumbLoads = 0;
  undoStack.value = [];
  thumbDone.value = 0;
  thumbTotal.value = 0;
  folder.value = "";
  kind.value = "photo";
  ffmpegAvailable.value = false;
  error.value = "";
  message.value = "";
}

onBeforeUnmount(() => observer?.disconnect());
</script>

<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#1a1a2e; color:#e0e0e0; }
.app { min-height:100vh; padding:16px; outline:none; }
header { display:flex; align-items:center; gap:16px; margin-bottom:16px; flex-wrap:wrap; }
h1 { font-size:18px; color:#00d4aa; }
.controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.folder-path { color:#888; font-size:13px; }
.kind-badge { background:#0f3460; color:#00d4aa; padding:3px 10px; border-radius:12px; font-size:12px; }
.warn-badge { background:#3a2a1a; color:#ffaa00; padding:3px 10px; border-radius:12px; font-size:12px; }
button { padding:6px 16px; border:1px solid #00d4aa; background:transparent; color:#00d4aa; border-radius:4px; cursor:pointer; }
button:hover { background:#00d4aa22; }
button:disabled { opacity:.5; cursor:not-allowed; }
button.active { background:#00d4aa; color:#1a1a2e; }
.reset-btn { border-color:#888; color:#888; }
.reset-btn:hover { background:#88888822; }
.undo-btn { border-color:#b488e0; color:#b488e0; }
.undo-btn:hover { background:#b488e022; }
.preview-btn { border-color:#4d9aff; color:#4d9aff; }
.preview-btn:hover { background:#4d9aff22; }

.stats { display:flex; gap:16px; margin-bottom:12px; padding:12px; background:#16213e; border-radius:8px; flex-wrap:wrap; }
.stat { display:flex; flex-direction:column; align-items:center; min-width:60px; }
.stat .label { font-size:11px; color:#888; }
.stat .value { font-size:18px; font-weight:bold; color:#00d4aa; }
.keep-stat .value { color:#35c46a; }
.drop-stat .value { color:#d94d4d; }
.unsure-stat .value { color:#f0b429; }
.pending-stat .value { color:#00d4aa; }

.action-bar { display:flex; align-items:center; gap:12px; margin-bottom:12px; padding:10px 14px; background:#0f3460; border-radius:8px; border-left:4px solid #00d4aa; flex-wrap:wrap; }
.action-bar.keep { border-left-color:#35c46a; }
.action-bar.drop { border-left-color:#d94d4d; }
.action-bar.unsure { border-left-color:#f0b429; }
.action-bar .current-badge { width:26px; height:26px; border-radius:50%; background:#000a; display:flex; align-items:center; justify-content:center; font-weight:bold; color:#00d4aa; }
.action-bar.keep .current-badge { color:#35c46a; }
.action-bar.drop .current-badge { color:#d94d4d; }
.action-bar.unsure .current-badge { color:#f0b429; }
.action-bar strong { color:#fff; }
.current-status-label { color:#aaa; font-size:13px; }
.action-buttons { display:flex; gap:6px; margin-left:auto; }
.hint { color:#888; font-size:12px; margin-left:8px; }

.loading { text-align:center; padding:20px; color:#ffaa00; }
.error { margin-bottom:12px; padding:12px; color:#ff8080; background:#2a1a1a; border-radius:8px; }
.message { margin-bottom:12px; padding:10px; color:#00d4aa; background:#113322; border-radius:8px; }

/* 区域 */
.zone { margin-bottom:16px; background:#16213e; border-radius:8px; padding:12px; display:flex; flex-direction:column; }
.zone-header { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
.zone-dot { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
.keep-dot { background:#35c46a; }
.unsure-dot { background:#f0b429; }
.drop-dot { background:#d94d4d; }
.zone-title { font-weight:600; color:#e0e0e0; font-size:14px; }
.zone-count { background:#00000066; padding:1px 9px; border-radius:10px; font-size:12px; color:#aaa; margin-left:auto; }
.zone-hint { color:#35c46a; font-size:13px; margin-left:auto; }
.pending-zone { min-height:200px; }
.pending-zone .zone-title { color:#00d4aa; }

/* 三分类区并排 */
.classified-row { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
.classified-scroll { flex:1; overflow-y:auto; min-height:220px; max-height:340px; padding-right:4px; }
.classified-grid { grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); }

/* 统一滚动条样式，避免 hover 时重排抖动 */
.classified-scroll::-webkit-scrollbar { width:8px; }
.classified-scroll::-webkit-scrollbar-track { background:transparent; }
.classified-scroll::-webkit-scrollbar-thumb { background:#2a3a5a; border-radius:4px; }
.classified-scroll:hover::-webkit-scrollbar-thumb { background:#3a4a6a; }

/* 网格 */
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:8px; }
.pending-grid { min-height:120px; }
.empty-hint { grid-column:1/-1; text-align:center; color:#555; padding:30px; font-size:13px; }

/* 卡片 */
.card { position:relative; aspect-ratio:1; border:2px solid transparent; border-radius:6px; overflow:hidden; background:#0d1b3a; cursor:pointer; transition:transform .08s, border-color .12s; }
.card:hover { transform:scale(1.02); }
.card.selected { border-color:#ff4d4d !important; box-shadow:0 0 0 2px #ff4d4d55; }
.card.keep { border-color:#35c46a33; }
.card.drop { opacity:.55; border-color:#d94d4d33; filter:saturate(.6); }
.card.unsure { border-color:#f0b42933; }
.card img { width:100%; height:100%; object-fit:cover; }
.placeholder { width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:22px; color:#555; }
.dot { color:#00d4aa; animation:b 1s infinite; }
@keyframes b { 0%,100%{opacity:.3} 50%{opacity:1} }
.card-name { position:absolute; bottom:0; left:0; right:0; height:18px; line-height:18px; font-size:9px; text-align:center; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding:0 4px; color:#ddd; background:#000000aa; }

/* 视频占位卡片 */
.video-ph { flex-direction:column; gap:6px; }
.video-icon { font-size:36px; }
.video-size { font-size:11px; color:#888; }

/* 类型选择弹窗 */
.modal-overlay { position:fixed; inset:0; background:#000a; display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:#16213e; border-radius:12px; padding:24px; min-width:360px; box-shadow:0 8px 32px #0008; }
.modal h3 { color:#00d4aa; margin-bottom:8px; }
.modal-desc { color:#888; font-size:13px; margin-bottom:16px; word-break:break-all; }
.kind-options { display:flex; gap:12px; margin-bottom:16px; }
.kind-option { flex:1; display:flex; flex-direction:column; align-items:center; gap:6px; padding:20px; border:1px solid #2a3a5a; background:#0d1b3a; border-radius:8px; cursor:pointer; }
.kind-option:hover { border-color:#00d4aa; background:#0f3460; }
.kind-icon { font-size:32px; }
.kind-label { color:#e0e0e0; font-weight:600; }
.kind-sub { color:#666; font-size:11px; }
.modal-cancel { width:100%; padding:8px; border-color:#555; color:#888; }

/* 预览按钮 */
.preview-btn { border-color:#00d4aa; color:#00d4aa; }
.preview-btn:hover { background:#00d4aa22; }
</style>

