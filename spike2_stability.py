"""
Spike 2: 评分稳定性测试
验证 InternVL3.5 在 temperature=0 + 固定 seed 下，同一张照片连续评估 3 次的结论一致率

用法:
  python spike2_stability.py <照片目录>

输出:
  spike-data/spike2_results.json      — 每张照片 3 次评估的完整记录
  spike-data/spike2_summary.json      — 汇总统计
"""

import os
import sys
import json
import time
import base64
import requests
from pathlib import Path
from datetime import datetime
from collections import Counter

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "bInternlVL35:8b"
NUM_RUNS = 3  # 每张照片评估次数

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

PROMPT_TEMPLATE = """评估这张照片的审美价值，按四个维度各给 0-100 整数分（不要小数）：
- 视觉吸引力 (visual)
- 情感共鸣 (emotion)
- 叙事性 (narrative)
- 稀缺性 (rarity)

并给出最终结论：keep / drop / unsure

只输出 JSON，不要输出任何其他内容：
{"visual": 0-100, "emotion": 0-100, "narrative": 0-100, "rarity": 0-100, "verdict": "keep|drop|unsure"}"""


def find_images(directory: str, limit: int = 30) -> list:
    """扫描目录，返回图片路径列表"""
    images = []
    seen_stems = set()
    raw_exts = {".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}

    for root, _, files in os.walk(directory):
        for f in sorted(files):
            ext = Path(f).suffix.lower()
            stem = Path(f).stem

            if ext in raw_exts:
                continue
            if ext not in IMAGE_EXTS:
                continue
            if stem in seen_stems:
                continue

            seen_stems.add(stem)
            images.append(os.path.join(root, f))

            if len(images) >= limit:
                return images

    return images


def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def evaluate_image(image_path: str, img_b64: str) -> dict:
    """调用模型评估单张照片"""
    payload = {
        "model": MODEL,
        "prompt": PROMPT_TEMPLATE,
        "images": [img_b64],
        "stream": False,
        "options": {
            "temperature": 0,
            "seed": 42,
            "top_k": 1,
            "num_predict": 300,
        }
    }

    start = time.time()
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - start

        raw_response = data.get("response", "").strip()
        parsed = parse_response(raw_response)

        return {
            "raw_response": raw_response,
            "parsed": parsed,
            "elapsed_s": round(elapsed, 2),
            "error": None,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "raw_response": "",
            "parsed": {"visual": None, "emotion": None, "narrative": None, "rarity": None, "verdict": "error"},
            "elapsed_s": round(elapsed, 2),
            "error": str(e),
        }


def parse_response(raw: str) -> dict:
    """从模型输出中解析四个维度分数和结论"""
    import re

    result = {"visual": None, "emotion": None, "narrative": None, "rarity": None, "verdict": "unsure"}

    # 尝试找 JSON 块
    json_match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
    if json_match:
        try:
            obj = json.loads(json_match.group())
            for dim in ("visual", "emotion", "narrative", "rarity"):
                if dim in obj and isinstance(obj[dim], (int, float)):
                    result[dim] = int(obj[dim])
            v = str(obj.get("verdict", "")).lower().strip()
            if v in ("keep", "drop", "unsure"):
                result["verdict"] = v
            return result
        except json.JSONDecodeError:
            pass

    # 模糊匹配 verdict
    lower = raw.lower()
    if "keep" in lower:
        result["verdict"] = "keep"
    elif "drop" in lower:
        result["verdict"] = "drop"

    # 模糊匹配分数
    for dim in ("visual", "emotion", "narrative", "rarity"):
        pattern = rf'"{dim}"\s*:\s*(\d+)'
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            result[dim] = int(m.group(1))

    return result


def main():
    if len(sys.argv) < 2:
        print("用法: python spike2_stability.py <照片目录> [数量限制，默认30]")
        sys.exit(1)

    photo_dir = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    if not os.path.isdir(photo_dir):
        print(f"错误: 目录不存在 — {photo_dir}")
        sys.exit(1)

    out_dir = os.path.join(os.path.dirname(__file__), "spike-data")
    os.makedirs(out_dir, exist_ok=True)

    images = find_images(photo_dir, limit=limit)
    print(f"找到 {len(images)} 张图片，每张评估 {NUM_RUNS} 次，共 {len(images) * NUM_RUNS} 次调用")
    print(f"模型: {MODEL}")
    print()

    if not images:
        print("错误: 目录中没有找到图片文件")
        sys.exit(1)

    all_results = []
    total_calls = len(images) * NUM_RUNS
    call_count = 0

    for i, img_path in enumerate(images):
        file_name = os.path.basename(img_path)
        print(f"[{i+1}/{len(images)}] {file_name}")

        # 预编码图片（3 次共用）
        img_b64 = encode_image(img_path)

        runs = []
        for run_idx in range(NUM_RUNS):
            call_count += 1
            print(f"  第 {run_idx+1}/{NUM_RUNS} 次...", end=" ", flush=True)
            result = evaluate_image(img_path, img_b64)
            runs.append(result)
            p = result["parsed"]
            print(f"{p['verdict']} v={p['visual']} e={p['emotion']} n={p['narrative']} r={p['rarity']} ({result['elapsed_s']}s)")

        # 计算这张照片的稳定性
        verdicts = [r["parsed"]["verdict"] for r in runs]
        verdict_consistent = len(set(verdicts)) == 1

        score_ranges = {}
        for dim in ("visual", "emotion", "narrative", "rarity"):
            scores = [r["parsed"][dim] for r in runs if r["parsed"][dim] is not None]
            if scores:
                score_ranges[dim] = max(scores) - min(scores)
            else:
                score_ranges[dim] = None

        file_result = {
            "file": file_name,
            "path": img_path,
            "runs": runs,
            "verdict_consistent": verdict_consistent,
            "verdicts": verdicts,
            "score_ranges": score_ranges,
        }
        all_results.append(file_result)

        # 每 5 张自动保存
        if (i + 1) % 5 == 0:
            save_results(all_results, out_dir)
            print(f"  (自动保存)")

    # 最终保存
    save_results(all_results, out_dir)
    print_summary(all_results)


def save_results(all_results: list, out_dir: str):
    """保存完整结果和汇总"""

    # 完整结果
    results_path = os.path.join(out_dir, "spike2_results.json")
    # 去掉 path 字段减小体积
    clean = []
    for r in all_results:
        c = dict(r)
        c.pop("path", None)
        clean.append(c)

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)

    # 汇总
    total = len(all_results)
    consistent_count = sum(1 for r in all_results if r["verdict_consistent"])
    consistent_pct = consistent_count / total * 100 if total else 0

    # 各维度抖动中位数
    from statistics import median
    dim_medians = {}
    for dim in ("visual", "emotion", "narrative", "rarity"):
        ranges = [r["score_ranges"][dim] for r in all_results if r["score_ranges"].get(dim) is not None]
        dim_medians[dim] = median(ranges) if ranges else None

    # JSON 解析成功率
    all_parsed = []
    for r in all_results:
        for run in r["runs"]:
            all_parsed.append(run["parsed"])
    parse_success = sum(1 for p in all_parsed if p["verdict"] != "error") / len(all_parsed) * 100 if all_parsed else 0

    # 平均耗时
    all_times = [run["elapsed_s"] for r in all_results for run in r["runs"] if run["error"] is None]
    avg_time = sum(all_times) / len(all_times) if all_times else 0

    verdict_dist = Counter(r["verdicts"][0] for r in all_results if r["verdicts"])

    summary = {
        "timestamp": datetime.now().isoformat(),
        "model": MODEL,
        "num_runs_per_photo": NUM_RUNS,
        "total_photos": total,
        "verdict_consistency": {
            "consistent_count": consistent_count,
            "consistent_pct": round(consistent_pct, 1),
            "threshold_pct": 95,
            "pass": consistent_pct >= 95,
        },
        "score_jitter": {
            "median_by_dim": {k: round(v, 1) if v is not None else None for k, v in dim_medians.items()},
            "overall_median": round(median([v for v in dim_medians.values() if v is not None]), 1) if any(v is not None for v in dim_medians.values()) else None,
            "threshold": 5,
            "pass": all((v is None or v <= 5) for v in dim_medians.values()),
        },
        "parse_success_rate_pct": round(parse_success, 1),
        "parse_success_pass": parse_success == 100,
        "avg_time_s": round(avg_time, 2),
        "verdict_distribution": dict(verdict_dist),
    }

    summary_path = os.path.join(out_dir, "spike2_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def print_summary(all_results: list):
    total = len(all_results)
    consistent_count = sum(1 for r in all_results if r["verdict_consistent"])
    consistent_pct = consistent_count / total * 100 if total else 0

    from statistics import median
    dim_medians = {}
    for dim in ("visual", "emotion", "narrative", "rarity"):
        ranges = [r["score_ranges"][dim] for r in all_results if r["score_ranges"].get(dim) is not None]
        dim_medians[dim] = median(ranges) if ranges else None

    all_parsed = []
    for r in all_results:
        for run in r["runs"]:
            all_parsed.append(run["parsed"])
    parse_success = sum(1 for p in all_parsed if p["verdict"] != "error") / len(all_parsed) * 100 if all_parsed else 0

    all_times = [run["elapsed_s"] for r in all_results for run in r["runs"] if run["error"] is None]
    avg_time = sum(all_times) / len(all_times) if all_times else 0

    print("\n" + "=" * 60)
    print("Spike 2 结果汇总")
    print("=" * 60)
    print(f"模型: {MODEL}")
    print(f"照片数: {total}, 每张评估 {NUM_RUNS} 次")
    print()

    print("--- 结论稳定性 ---")
    print(f"  结论一致率: {consistent_count}/{total} = {consistent_pct:.1f}%")
    print(f"  决策门 ≥ 95%: {'✅ 通过' if consistent_pct >= 95 else '❌ 不通过'}")

    # 不一致的案例
    inconsistent = [r for r in all_results if not r["verdict_consistent"]]
    if inconsistent:
        print(f"  不一致照片 ({len(inconsistent)} 张):")
        for r in inconsistent[:5]:
            print(f"    {r['file']}: {r['verdicts']}")

    print()
    print("--- 分数抖动 ---")
    for dim, med in dim_medians.items():
        status = "✅" if med is not None and med <= 5 else "⚠️" if med is not None else "❓"
        print(f"  {dim:10s}: 极差中位数 = {med:.1f} {status}")
    overall = median([v for v in dim_medians.values() if v is not None]) if any(v is not None for v in dim_medians.values()) else None
    if overall is not None:
        print(f"  {'整体':10s}: 极差中位数 = {overall:.1f} {'✅' if overall <= 5 else '❌'} (阈值 ≤ 5)")

    print()
    print("--- 其他指标 ---")
    print(f"  JSON 解析成功率: {parse_success:.1f}% {'✅' if parse_success == 100 else '❌'} (阈值 100%)")
    print(f"  平均单次耗时: {avg_time:.2f}s")

    print()
    if consistent_pct >= 95:
        print("✅ 评分稳定性达标，设计文档 §4.8 约束有效")
    else:
        print("❌ 评分稳定性不达标，需启用备用方案：")
        print("   1. 增加 self-consistency 投票次数至 5")
        print("   2. 强制分数对齐到 5 的倍数")
        print("   3. 考虑换模型")


if __name__ == "__main__":
    main()
