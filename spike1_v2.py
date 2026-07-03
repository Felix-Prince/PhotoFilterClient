"""
Spike 1: 模型分流可行性测试（修正版）
- 用 PIL 生成缩略图再传给 Ollama（避免传 11MB 原图）
- 用 /api/chat 端点（更标准）
- 修复 Windows 终端编码问题
"""

import os, sys, json, csv, time, base64, requests
from pathlib import Path
from datetime import datetime
from io import BytesIO

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:e4b"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
RAW_EXTS = {".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}
THUMB_SIZE = 512  # 缩略图尺寸，够模型判断即可

PROMPT_TEXT = """你是一个摄影筛选助手。请快速判断这张照片：
- 倾向保留 (keep)：直觉上有亮点，值得留下
- 倾向淘汰 (drop)：明显平庸或有硬伤
- 不确定 (unsure)：拿不准，需要细看

只输出一个 JSON，不要输出任何其他内容：
{"verdict": "keep" | "drop" | "unsure", "reason": "<一句话中文理由>"}"""


def make_thumbnail_b64(image_path: str) -> str:
    """用 PIL 生成缩略图并返回 base64"""
    from PIL import Image
    img = Image.open(image_path)
    img.thumbnail((THUMB_SIZE, THUMB_SIZE))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def find_images(directory: str, limit: int = 100) -> list:
    images = []
    seen_stems = set()
    for root, _, files in os.walk(directory):
        for f in sorted(files):
            ext = Path(f).suffix.lower()
            stem = Path(f).stem
            if ext in RAW_EXTS or ext not in IMAGE_EXTS:
                continue
            if stem in seen_stems:
                continue
            seen_stems.add(stem)
            images.append(os.path.join(root, f))
            if len(images) >= limit:
                return images
    return images


def classify_image(image_path: str) -> dict:
    try:
        img_b64 = make_thumbnail_b64(image_path)
    except Exception as e:
        return {"file": os.path.basename(image_path), "verdict": "error",
                "reason": "", "elapsed_s": 0, "error": f"thumbnail failed: {e}"}

    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": PROMPT_TEXT,
            "images": [img_b64]
        }],
        "stream": False,
        "options": {"temperature": 0, "seed": 42}
    }

    start = time.time()
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - start
        raw = data.get("message", {}).get("content", "").strip()
        verdict, reason = parse_response(raw)
        return {"file": os.path.basename(image_path), "verdict": verdict,
                "reason": reason, "elapsed_s": round(elapsed, 2), "error": None}
    except Exception as e:
        return {"file": os.path.basename(image_path), "verdict": "error",
                "reason": "", "elapsed_s": round(time.time() - start, 2), "error": str(e)}


def parse_response(raw: str) -> tuple:
    import re
    try:
        obj = json.loads(raw)
        v = obj.get("verdict", "unsure").lower().strip()
        r = obj.get("reason", "")
        if v in ("keep", "drop", "unsure"):
            return v, r
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{[^}]+\}', raw, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            v = obj.get("verdict", "unsure").lower().strip()
            r = obj.get("reason", "")
            if v in ("keep", "drop", "unsure"):
                return v, r
        except json.JSONDecodeError:
            pass
    lower = raw.lower()
    if "keep" in lower: return "keep", raw[:100]
    if "drop" in lower: return "drop", raw[:100]
    return "unsure", raw[:100]


def main():
    if len(sys.argv) < 2:
        print("Usage: python spike1_v2.py <photo_dir> [limit]")
        sys.exit(1)

    photo_dir = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    if not os.path.isdir(photo_dir):
        print(f"Error: dir not found - {photo_dir}")
        sys.exit(1)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike-data")
    os.makedirs(out_dir, exist_ok=True)

    images = find_images(photo_dir, limit)
    print(f"Found {len(images)} images, starting classification...")
    print(f"Model: {MODEL}, Thumbnail: {THUMB_SIZE}px")

    results = []
    for i, img_path in enumerate(images):
        fname = os.path.basename(img_path)
        print(f"[{i+1}/{len(images)}] {fname}...", end=" ", flush=True)
        result = classify_image(img_path)
        results.append(result)
        status = result['verdict']
        t = result['elapsed_s']
        err = f" ERR:{result['error'][:60]}" if result['error'] else ""
        print(f"{status} ({t}s){err}")

        if (i + 1) % 10 == 0:
            save(results, out_dir)

    save(results, out_dir)
    print_summary(results)


def save(results, out_dir):
    csv_path = os.path.join(out_dir, "spike1_results.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file","verdict","reason","elapsed_s","error"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k,"") for k in ["file","verdict","reason","elapsed_s","error"]})

    total = len(results)
    vc = {}
    for r in results:
        v = r["verdict"]
        vc[v] = vc.get(v, 0) + 1

    valid = [r for r in results if r["error"] is None]
    avg_t = sum(r["elapsed_s"] for r in valid) / len(valid) if valid else 0
    unsure_pct = vc.get("unsure", 0) / total * 100 if total else 0

    summary = {
        "timestamp": datetime.now().isoformat(), "model": MODEL,
        "total": total, "verdicts": vc,
        "unsure_pct": round(unsure_pct, 1),
        "avg_time_s": round(avg_t, 2),
        "pass_unsure": unsure_pct < 40,
        "pass_time": avg_t < 3,
    }
    with open(os.path.join(out_dir, "spike1_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def print_summary(results):
    total = len(results)
    vc = {}
    for r in results:
        v = r["verdict"]
        vc[v] = vc.get(v, 0) + 1

    valid = [r for r in results if r["error"] is None]
    avg_t = sum(r["elapsed_s"] for r in valid) / len(valid) if valid else 0
    unsure_pct = vc.get("unsure", 0) / total * 100 if total else 0

    print("\n" + "=" * 50)
    print("Spike 1 Summary")
    print("=" * 50)
    print(f"Model: {MODEL}")
    print(f"Photos: {total}")
    for v in ["keep", "drop", "unsure", "error"]:
        c = vc.get(v, 0)
        print(f"  {v:8s}: {c:4d} ({c/total*100:5.1f}%)" if total else f"  {v:8s}: 0")
    print(f"  Avg time: {avg_t:.2f}s")
    print()
    print(f"  unsure < 40%: {'PASS' if unsure_pct < 40 else 'FAIL'} ({unsure_pct:.1f}%)")
    print(f"  avg < 3s:     {'PASS' if avg_t < 3 else 'FAIL'} ({avg_t:.2f}s)")

    if vc.get("error", 0) == total:
        print("\n  ALL ERRORS - check Ollama / model availability")
    elif unsure_pct >= 40:
        print("\n  unsure too high - consider single-layer (InternVL3.5 only)")
    else:
        print("\n  Two-layer architecture viable")


if __name__ == "__main__":
    main()
