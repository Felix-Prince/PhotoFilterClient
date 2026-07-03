"""
Spike 1: 模型分流可行性测试
验证 gemma4:e4b 对"倾向保留/倾向淘汰/不确定"三分类的分流是否有意义

用法:
  python spike1_classify.py <照片目录>

输出:
  spike-data/spike1_results.csv       — 每张照片的分流结果
  spike-data/spike1_summary.json      — 汇总统计
"""

import os
import sys
import json
import csv
import time
import base64
import requests
from pathlib import Path
from datetime import datetime

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma4:e4b"

# 图片扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

PROMPT_TEMPLATE = """你是一个摄影筛选助手。请快速判断这张照片：
- 倾向保留 (keep)：直觉上有亮点，值得留下
- 倾向淘汰 (drop)：明显平庸或有硬伤
- 不确定 (unsure)：拿不准，需要细看

只输出一个 JSON，不要输出任何其他内容：
{"verdict": "keep" | "drop" | "unsure", "reason": "<一句话中文理由>"}"""


def find_images(directory: str, limit: int = 100) -> list:
    """扫描目录中的图片文件，优先选 JPG（跳过 RAW 避免重复）"""
    images = []
    seen_stems = set()
    raw_exts = {".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}

    for root, _, files in os.walk(directory):
        for f in sorted(files):
            ext = Path(f).suffix.lower()
            stem = Path(f).stem

            # 跳过 RAW 文件（用同名 JPG 代替）
            if ext in raw_exts:
                continue
            if ext not in IMAGE_EXTS:
                continue
            # 去重：同名文件只取一个
            if stem in seen_stems:
                continue

            seen_stems.add(stem)
            images.append(os.path.join(root, f))

            if len(images) >= limit:
                return images

    return images


def encode_image(image_path: str) -> str:
    """将图片编码为 base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def classify_image(image_path: str) -> dict:
    """调用 Ollama 对单张图片做三分类"""
    img_b64 = encode_image(image_path)

    payload = {
        "model": MODEL,
        "prompt": PROMPT_TEMPLATE,
        "images": [img_b64],
        "stream": False,
        "options": {
            "temperature": 0,
            "seed": 42,
            "num_predict": 200,
        }
    }

    start = time.time()
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - start

        raw_response = data.get("response", "").strip()

        # 尝试从响应中提取 JSON
        verdict, reason = parse_response(raw_response)

        return {
            "file": os.path.basename(image_path),
            "path": image_path,
            "verdict": verdict,
            "reason": reason,
            "raw_response": raw_response,
            "elapsed_s": round(elapsed, 2),
            "error": None,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "file": os.path.basename(image_path),
            "path": image_path,
            "verdict": "error",
            "reason": "",
            "raw_response": "",
            "elapsed_s": round(elapsed, 2),
            "error": str(e),
        }


def parse_response(raw: str) -> tuple:
    """从模型输出中提取 verdict 和 reason"""
    # 尝试直接解析整个响应为 JSON
    try:
        obj = json.loads(raw)
        v = obj.get("verdict", "unsure").lower().strip()
        r = obj.get("reason", "")
        if v in ("keep", "drop", "unsure"):
            return v, r
    except json.JSONDecodeError:
        pass

    # 尝试从响应中找到 JSON 块
    import re
    json_match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
    if json_match:
        try:
            obj = json.loads(json_match.group())
            v = obj.get("verdict", "unsure").lower().strip()
            r = obj.get("reason", "")
            if v in ("keep", "drop", "unsure"):
                return v, r
        except json.JSONDecodeError:
            pass

    # 模糊匹配关键词
    lower = raw.lower()
    if "keep" in lower or "保留" in lower:
        return "keep", raw[:100]
    if "drop" in lower or "淘汰" in lower or "删除" in lower:
        return "drop", raw[:100]

    return "unsure", raw[:100]


def main():
    if len(sys.argv) < 2:
        print("用法: python spike1_classify.py <照片目录> [数量限制，默认100]")
        sys.exit(1)

    photo_dir = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    if not os.path.isdir(photo_dir):
        print(f"错误: 目录不存在 — {photo_dir}")
        sys.exit(1)

    # 创建输出目录
    out_dir = os.path.join(os.path.dirname(__file__), "spike-data")
    os.makedirs(out_dir, exist_ok=True)

    # 扫描图片
    images = find_images(photo_dir, limit=limit)
    print(f"找到 {len(images)} 张图片，开始分流测试...")

    if not images:
        print("错误: 目录中没有找到图片文件")
        sys.exit(1)

    # 逐张分类
    results = []
    for i, img_path in enumerate(images):
        print(f"[{i+1}/{len(images)}] {os.path.basename(img_path)}...", end=" ", flush=True)
        result = classify_image(img_path)
        results.append(result)
        print(f"{result['verdict']} ({result['elapsed_s']}s)")

        # 每 10 张自动保存一次，防中断丢数据
        if (i + 1) % 10 == 0:
            save_results(results, out_dir)

    # 最终保存
    save_results(results, out_dir)
    print_summary(results)


def save_results(results: list, out_dir: str):
    """保存结果到 CSV 和汇总 JSON"""
    # CSV
    csv_path = os.path.join(out_dir, "spike1_results.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "file", "verdict", "reason", "elapsed_s", "error"
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "file": r["file"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "elapsed_s": r["elapsed_s"],
                "error": r["error"] or "",
            })

    # 汇总 JSON
    verdicts = [r["verdict"] for r in results]
    total = len(results)
    keep_count = verdicts.count("keep")
    drop_count = verdicts.count("drop")
    unsure_count = verdicts.count("unsure")
    error_count = verdicts.count("error")

    valid_results = [r for r in results if r["error"] is None]
    avg_time = sum(r["elapsed_s"] for r in valid_results) / len(valid_results) if valid_results else 0
    max_time = max((r["elapsed_s"] for r in valid_results), default=0)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "model": MODEL,
        "total_photos": total,
        "keep": keep_count,
        "keep_pct": round(keep_count / total * 100, 1) if total else 0,
        "drop": drop_count,
        "drop_pct": round(drop_count / total * 100, 1) if total else 0,
        "unsure": unsure_count,
        "unsure_pct": round(unsure_count / total * 100, 1) if total else 0,
        "error": error_count,
        "avg_time_s": round(avg_time, 2),
        "max_time_s": round(max_time, 2),
        # 决策门
        "decision_gate": {
            "unsure_pct_threshold": 40,
            "unsure_pct_actual": round(unsure_count / total * 100, 1) if total else 0,
            "unsure_pass": unsure_count / total * 100 < 40 if total else False,
            "avg_time_threshold_s": 3,
            "avg_time_actual_s": round(avg_time, 2),
            "avg_time_pass": avg_time < 3,
        }
    }

    json_path = os.path.join(out_dir, "spike1_summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def print_summary(results: list):
    """打印汇总信息"""
    verdicts = [r["verdict"] for r in results]
    total = len(results)
    keep_count = verdicts.count("keep")
    drop_count = verdicts.count("drop")
    unsure_count = verdicts.count("unsure")
    error_count = verdicts.count("error")

    valid = [r for r in results if r["error"] is None]
    avg_time = sum(r["elapsed_s"] for r in valid) / len(valid) if valid else 0

    print("\n" + "=" * 60)
    print("Spike 1 结果汇总")
    print("=" * 60)
    print(f"模型: {MODEL}")
    print(f"照片数: {total}")
    print(f"  保留 (keep):  {keep_count:4d} ({keep_count/total*100:5.1f}%)")
    print(f"  淘汰 (drop):  {drop_count:4d} ({drop_count/total*100:5.1f}%)")
    print(f"  不确定(unsure): {unsure_count:4d} ({unsure_count/total*100:5.1f}%)")
    print(f"  错误 (error): {error_count:4d} ({error_count/total*100:5.1f}%)")
    print(f"  平均耗时: {avg_time:.2f}s")
    print()

    unsure_pct = unsure_count / total * 100 if total else 0
    print("--- 决策门 ---")
    print(f"  unsure 占比 < 40%: {'✅ 通过' if unsure_pct < 40 else '❌ 不通过'} (实际 {unsure_pct:.1f}%)")
    print(f"  平均耗时 < 3s:     {'✅ 通过' if avg_time < 3 else '❌ 不通过'} (实际 {avg_time:.2f}s)")
    print(f"  一致率: 需人工标注后计算（请用 spike1_annotate.py）")
    print()

    if unsure_pct >= 40:
        print("⚠️  unsure 占比过高，建议改为单层架构（全量走 InternVL3.5）")
    elif unsure_pct >= 25:
        print("⚠️  unsure 占比偏高，轻量层分流效果有限，建议关注一致率后再决定")
    else:
        print("✅ 轻量预判层分流有效，建议保留双层架构")


if __name__ == "__main__":
    main()
