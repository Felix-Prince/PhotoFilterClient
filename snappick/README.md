# SnapPick

本地优先的照片初筛工具。当前处于阶段零：项目骨架 + 文件夹扫描 + 缩略图浏览。

## 技术栈

- Tauri v2
- Rust 后端
- Vue 3 + Vite 前端
- Ollama / InternVL3.5（后续阶段接入）

## 当前能力

- 选择本地照片目录
- 扫描 JPG/PNG 等预览图，自动跳过 RAW（CR3/NEF/ARW 等）
- 检测同名 RAW 配对
- 异步生成缩略图，不阻塞 UI
- 可滚动浏览缩略图

## 开发运行

```bash
npm install
npm run tauri:dev
```

## Spike 结论

- gemma4:e4b 轻量预判层不可行：63 张同场景照片 100% keep，平均 6.58s/张。
- InternVL3.5 稳定性达标：30 张 × 3 次，结论一致率 100%，平均 2.76s/次。
- 缩略图加载必须异步；同步 Tauri command 会冻结 UI。

详见根目录的 `PhotoFilterSpike.md`。
