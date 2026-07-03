<template>
  <div class="app">
    <header>
      <h1>🔬 Spike 3 — 缩略图性能测试</h1>
      <div class="controls">
        <button @click="selectFolder" :disabled="loading">选择照片目录</button>
        <button @click="clearResults">清空</button>
        <span v-if="folder">📁 {{ folder }}</span>
      </div>
    </header>

    <div class="stats" v-if="photos.length > 0">
      <div class="stat"><span class="label">照片总数</span><span class="value">{{ photos.length }}</span></div>
      <div class="stat"><span class="label">已加载</span><span class="value">{{ loadedCount }} / {{ photos.length }}</span></div>
      <div class="stat"><span class="label">扫描</span><span class="value">{{ scanMs ?? '—' }}ms</span></div>
      <div class="stat"><span class="label">首屏(20张)</span><span class="value">{{ firstScreenMs ?? '—' }}ms</span></div>
    </div>

    <div v-if="loading" class="loading">{{ loadingMsg }}</div>
    <div v-if="error" class="error">❌ {{ error }}</div>

    <div class="grid" ref="gridRef">
      <div v-for="(photo, idx) in photos" :key="photo.name" class="card" :data-idx="idx">
        <img v-if="photo.dataUrl" :src="photo.dataUrl" :alt="photo.name" @load="onLoad(idx)" />
        <div v-else-if="photo.loading" class="placeholder dot">...</div>
        <div v-else class="placeholder">⏳</div>
        <div class="card-name">{{ photo.name }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onBeforeUnmount } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

const photos = ref([]);
const loading = ref(false);
const loadingMsg = ref("");
const folder = ref("");
const error = ref("");
const scanMs = ref(null);
const firstScreenMs = ref(null);
const loadedCount = ref(0);
const gridRef = ref(null);

let t0 = 0;
let loaded = new Set();
let observer = null;
const pending = new Set();
let active = 0;
const MAX = 3;

async function selectFolder() {
  const selected = await open({ directory: true, multiple: false });
  if (!selected) return;

  folder.value = selected;
  loading.value = true;
  error.value = "";
  photos.value = [];
  loadedCount.value = 0;
  loaded.clear();
  pending.clear();
  active = 0;
  firstScreenMs.value = null;
  scanMs.value = null;

  t0 = performance.now();
  loadingMsg.value = "扫描中...";

  try {
    const r = await invoke("scan_photos", { dirPath: selected });
    scanMs.value = r.scan_ms;
    photos.value = r.photos.map(p => ({ ...p, dataUrl: null, loading: false }));
    loading.value = false;
    await nextTick();
    startObserver();
  } catch (e) {
    error.value = String(e);
    loading.value = false;
  }
}

function startObserver() {
  if (observer) observer.disconnect();
  observer = new IntersectionObserver(entries => {
    for (const e of entries) {
      if (e.isIntersecting) {
        const i = parseInt(e.target.dataset.idx);
        if (!isNaN(i)) schedule(i);
      }
    }
  }, { rootMargin: "200px", threshold: 0 });

  gridRef.value?.querySelectorAll(".card").forEach(c => observer.observe(c));
}

function schedule(i) {
  const p = photos.value[i];
  if (!p || p.dataUrl || p.loading || pending.has(i)) return;
  if (active < MAX) {
    load(i);
  } else {
    pending.add(i);
  }
}

function next() {
  while (active < MAX && pending.size > 0) {
    const i = pending.values().next().value;
    pending.delete(i);
    const p = photos.value[i];
    if (p && !p.dataUrl && !p.loading) load(i);
  }
}

async function load(i) {
  const p = photos.value[i];
  if (!p || p.dataUrl || p.loading) return;
  active++;
  p.loading = true;
  try {
    const r = await invoke("get_thumbnail", { photoPath: p.path });
    if (r.data_url) p.dataUrl = r.data_url;
  } catch (e) {
    console.error("thumb err:", i, e);
  } finally {
    p.loading = false;
    active--;
    next();
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

function clearResults() {
  if (observer) observer.disconnect();
  photos.value = [];
  loadedCount.value = 0;
  loaded.clear();
  pending.clear();
  active = 0;
  folder.value = "";
  error.value = "";
}

onBeforeUnmount(() => observer?.disconnect());
</script>

<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#1a1a2e; color:#e0e0e0; }
.app { padding:16px; }
header { display:flex; align-items:center; gap:16px; margin-bottom:16px; flex-wrap:wrap; }
h1 { font-size:18px; color:#00d4aa; }
.controls { display:flex; gap:8px; align-items:center; }
button { padding:6px 16px; border:1px solid #00d4aa; background:transparent; color:#00d4aa; border-radius:4px; cursor:pointer; }
button:hover { background:#00d4aa22; }
button:disabled { opacity:.5; cursor:not-allowed; }
.stats { display:flex; gap:24px; margin-bottom:16px; padding:12px; background:#16213e; border-radius:8px; flex-wrap:wrap; }
.stat { display:flex; flex-direction:column; align-items:center; min-width:80px; }
.stat .label { font-size:11px; color:#888; }
.stat .value { font-size:18px; font-weight:bold; color:#00d4aa; }
.loading { text-align:center; padding:20px; color:#ffaa00; }
.error { text-align:center; padding:20px; color:#ff4444; background:#2a1a1a; border-radius:8px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:8px; }
.card { aspect-ratio:1; border-radius:6px; overflow:hidden; background:#16213e; display:flex; flex-direction:column; }
.card img { width:100%; height:calc(100% - 20px); object-fit:cover; }
.placeholder { width:100%; height:calc(100% - 20px); display:flex; align-items:center; justify-content:center; font-size:24px; }
.dot { color:#00d4aa; animation:b 1s infinite; }
@keyframes b { 0%,100%{opacity:.3} 50%{opacity:1} }
.card-name { height:20px; line-height:20px; font-size:10px; text-align:center; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding:0 4px; color:#888; }
</style>
