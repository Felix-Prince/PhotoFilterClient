"""
Spike 1 辅助脚本：人工标注工具
对 Spike 1 的结果进行人工标注，计算模型与人工的一致率

用法:
  python spike1_annotate.py

交互式逐张显示模型判断，由用户输入自己的判断 (k=keep / d=drop / u=unsure / s=skip)
"""

import os
import json
import csv

SPIKE_DATA_DIR = os.path.join(os.path.dirname(__file__), "spike-data")
RESULTS_CSV = os.path.join(SPIKE_DATA_DIR, "spike1_results.csv")
ANNOTATIONS_CSV = os.path.join(SPIKE_DATA_DIR, "spike1_annotations.csv")
SUMMARY_JSON = os.path.join(SPIKE_DATA_DIR, "spike1_summary.json")


def load_results():
    if not os.path.exists(RESULTS_CSV):
        print(f"错误: 找不到 {RESULTS_CSV}")
        print("请先运行 spike1_classify.py")
        return []

    results = []
    with open(RESULTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            results.append(row)
    return results


def load_existing_annotations():
    """加载已有的标注，支持中断续标"""
    if not os.path.exists(ANNOTATIONS_CSV):
        return {}

    annotations = {}
    with open(ANNOTATIONS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            annotations[row["file"]] = row["human_verdict"]
    return annotations


def save_annotations(annotations: dict):
    with open(ANNOTATIONS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "human_verdict"])
        writer.writeheader()
        for file, verdict in annotations.items():
            writer.writerow({"file": file, "human_verdict": verdict})


def update_summary(annotations: dict, results: list):
    """更新 spike1_summary.json，加入一致率"""
    with open(SUMMARY_JSON, "r", encoding="utf-8") as f:
        summary = json.load(f)

    # 计算一致率（排除 unsure 和 error）
    match_count = 0
    compared_count = 0
    for r in results:
        file = r["file"]
        model_verdict = r["verdict"]
        human_verdict = annotations.get(file)
        if human_verdict and human_verdict != "skip" and model_verdict not in ("error",):
            compared_count += 1
            if model_verdict == human_verdict:
                match_count += 1

    agreement_rate = match_count / compared_count * 100 if compared_count else 0

    summary["human_annotation"] = {
        "annotated_count": len([v for v in annotations.values() if v != "skip"]),
        "compared_count": compared_count,
        "match_count": match_count,
        "agreement_rate_pct": round(agreement_rate, 1),
        "agreement_threshold_pct": 75,
        "agreement_pass": agreement_rate >= 75,
    }

    with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n一致率: {match_count}/{compared_count} = {agreement_rate:.1f}%")
    print(f"决策门 ≥ 75%: {'✅ 通过' if agreement_rate >= 75 else '❌ 不通过'}")


def main():
    results = load_results()
    if not results:
        return

    annotations = load_existing_annotations()
    already_done = set(annotations.keys())

    remaining = [r for r in results if r["file"] not in already_done and r["verdict"] != "error"]
    print(f"共 {len(results)} 张，已标注 {len(already_done)} 张，剩余 {len(remaining)} 张")
    print("输入: k=保留 / d=淘汰 / u=不确定 / s=跳过 / q=保存退出")
    print("-" * 60)

    for i, r in enumerate(remaining):
        print(f"\n[{len(already_done) + i + 1}/{len(results)}] {r['file']}")
        print(f"  模型判断: {r['verdict']} — {r['reason']}")

        while True:
            choice = input("  你的判断 (k/d/u/s/q): ").strip().lower()
            if choice in ("k", "keep"):
                annotations[r["file"]] = "keep"
                break
            elif choice in ("d", "drop"):
                annotations[r["file"]] = "drop"
                break
            elif choice in ("u", "unsure"):
                annotations[r["file"]] = "unsure"
                break
            elif choice in ("s", "skip"):
                annotations[r["file"]] = "skip"
                break
            elif choice in ("q", "quit"):
                save_annotations(annotations)
                update_summary(annotations, results)
                print("已保存并退出")
                return
            else:
                print("  无效输入，请用 k/d/u/s/q")

        # 每 10 张自动保存
        if (i + 1) % 10 == 0:
            save_annotations(annotations)
            print(f"  (自动保存)")

    # 全部标注完成
    save_annotations(annotations)
    update_summary(annotations, results)
    print("\n✅ 全部标注完成！")


if __name__ == "__main__":
    main()
