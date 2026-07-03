# SnapPick — 摄影意图理解的照片初筛工具

> 理解摄影意图的照片筛选助手，而非简单的照片质量评分器。核心价值是减少人工筛选的工作量。

[![Version](https://img.shields.io/badge/version-v0.1.0-00d4aa)]() [![Stage](https://img.shields.io/badge/stage-阶段一完成-0f3460)]() [![Stack](https://img.shields.io/badge/Tauri-v2-FFC131)]() [![License](https://img.shields.io/badge/license-MIT-blue)]()

---

## 📖 项目简介

日常摄影单次拍摄数百至上千张照片，逐张人工筛选耗时费力。SnapPick 先做**初筛**，给出建议和理由，用户再做精细化挑选。

**定位**：工具只做初筛，不做终裁；给出建议和理由，最终决定权留给用户；边界照片宁可多保留，不误杀。

**核心理念**：审美驱动 + 技术辅助。技术是底线，审美是决定因素。

---

## ✨ 当前版本功能（v0.1.0 · 阶段一）

本版本是**手动快筛**版本——不接 AI，纯键盘操作快速筛选，操作即文件移动。

- 📁 **图片 / 视频统一筛选**：选目录后选择筛选类型，各自独立分类
- ⌨️ **键盘快筛**：P 保留 / X 删除 / U 待定，←/→ 切换，Tab 跳下一待处理
- 📂 **三区域布局**：待处理区（主区域）+ 保留/待定/删除 三分类区并排
- 🖼 **缩略图懒加载**：异步生成 + 3 路并发，130 张照片流畅不卡
- 🎬 **视频抽帧**：检测到 ffmpeg 自动抽首帧做缩略图，无则显示文件名卡片
- 🔍 **预览大图**：双击卡片或 Enter 调用系统默认查看器/播放器
- ↩️ **撤销栈**：Ctrl+Z 撤销最近 50 步操作，含失败回滚
- 🔁 **断点续传**：关软件重开自动恢复已分类状态
- 📦 **配对原子移动**：JPG+RAW+XMP 配对整体移动，失败自动回滚
- 🏷 **容器隔离**：`图片/{保留,删除,待定}` 与 `视频/{保留,删除,待定}` 同目录互不干扰

---

## 🛠 技术栈

| 层 | 选型 |
|----|------|
| 桌面框架 | Tauri v2 |
| 后端 | Rust |
| 前端 | Vue 3 + Vite |
| 图片处理 | `image` crate |
| 视频抽帧 | 系统 ffmpeg（可选） |
| 系统查看器 | `open` crate |

---

## 🚀 快速开始

### 环境要求

- [Node.js](https://nodejs.org/) ≥ 18
- [Rust](https://www.rust-lang.org/) ≥ 1.77
- [ffmpeg](https://ffmpeg.org/)（可选，视频抽帧缩略图需要；无则视频仅显示文件名卡片）

### 运行

```bash
cd snappick
npm install
npm run tauri:dev
```

### 打包

```bash
cd snappick
npm run tauri:build
```

---

## 📁 项目结构

```
PhotoFilterClient/
├── PhotoFilterDesign.md          # 顶层设计文档（方法论 + 6 命题）
├── PhotoFilterArchitectureUI.md  # 技术架构与界面设计
├── PhotoFilterApplyPath.md       # 实现路线规划（5 阶段）
├── PhotoFilterSpike.md           # Spike 风险验证文档（已完成）
├── spike1_v2.py                  # Spike 1：轻量预判可行性
├── spike2_stability.py           # Spike 2：评分稳定性测试
├── spike3-app/                   # Spike 3：Tauri 缩略图性能验证
└── snappick/                     # 主应用
    ├── src/                      # Vue 3 前端
    │   ├── App.vue
    │   └── main.js
    ├── src-tauri/                # Rust 后端
    │   ├── src/lib.rs            # 扫描/缩略图/移动/撤销/预览
    │   └── Cargo.toml
    └── package.json
```

---

## 📊 开发路线

| 阶段 | 名称 | 状态 |
|------|------|------|
| Spike | 风险验证 | ✅ 完成 |
| 零 | 项目骨架 | ✅ 完成 |
| 一 | 手动快筛 | ✅ 完成（**当前版本**）|
| 二 | 技术初筛 | 📋 待开始 |
| 三 | AI 审美评估 | 📋 待开始 |
| 四 | 智能分组 | 📋 待开始 |
| 五 | 学习个性化 | 📋 待开始 |

详见 [PhotoFilterApplyPath.md](PhotoFilterApplyPath.md)。

---

## 🔬 Spike 阶段关键结论

开工前用 3 个 Spike 验证了关键不确定性，避免带病开工：

| Spike | 假设 | 结论 |
|-------|------|------|
| 1 | gemma4:e4b 可做轻量预判 | ❌ 63 张同场景照片 100% 输出 keep，零区分力 → 取消轻量层，全量走 InternVL3.5 |
| 2 | InternVL3.5 评分稳定 | ✅ 30 张 × 3 次一致率 100%，抖动 0 → 稳定性约束有效 |
| 3 | Tauri 跑 800 张缩略图流畅 | ✅ 异步命令 + 逐张 base64 + 3 路并发，130 张实测稳定 |

详见 [PhotoFilterSpike.md](PhotoFilterSpike.md)。

---

## ⚙️ 核心设计决策

- **状态由文件位置决定**：照片在哪个子文件夹就是它的状态，关软件不丢
- **配对原子移动**：JPG+RAW+XMP 整体移动，任一失败回滚已移动项
- **Tauri 命令必须异步**：CPU 密集任务用 `spawn_blocking` 避免冻结 UI
- **缩略图逐张加载**：避免大批量 base64 通过 IPC 传输导致崩溃
- **容器隔离**：图片/视频各自容器，内部文件夹名统一 `保留/删除/待定`

详见 [PhotoFilterDesign.md](PhotoFilterDesign.md)。

---

## 🔧 后续阶段预告

- **阶段二**：技术初筛（硬过滤、Laplacian 模糊检测、EXIF、XMP 写入）
- **阶段三**：AI 审美评估（InternVL3.5，本地 Ollama，审美档案 + 四维度评分）
- **阶段四**：智能分组（相似照片自动分组，连拍一键选优）
- **阶段五**：学习个性化（越用越懂你的偏好）

---

## 📄 许可证

MIT License

---

## 📝 文档版本

- 设计文档 v1.5
- 架构文档 v1.4
- 路线文档 v1.4
- Spike 文档 v1.0
- 应用 v0.1.0（阶段一）
