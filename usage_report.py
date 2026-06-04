import argparse
import json
import csv
from collections import defaultdict
from pathlib import Path

def summarize_usage(
    log_path: str | None = None,
    output_markdown: bool = True,
    output_csv: bool = True
) -> dict:
    if not log_path:
        log_path = "logs/llm_usage.jsonl"
    
    log_file = Path(log_path)
    if not log_file.exists():
        print(f"Log file not found: {log_path}")
        return {}

    records = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                    
    if not records:
        print("No valid records found in log file.")
        return {}
        
    total_reqs = len(records)
    total_prompt = sum(r.get("prompt_tokens", 0) or 0 for r in records)
    total_cached = sum(r.get("cached_tokens", 0) or 0 for r in records)
    total_comp = sum(r.get("completion_tokens", 0) or 0 for r in records)
    total_reasoning = sum(r.get("reasoning_tokens", 0) or 0 for r in records)
    total_tokens = sum(r.get("total_tokens", 0) or 0 for r in records)
    
    latencies = [r.get("latency_ms", 0) or 0 for r in records if (r.get("latency_ms", 0) or 0) > 0]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    
    success_count = sum(1 for r in records if r.get("success", False))
    json_valid_count = sum(1 for r in records if r.get("json_valid", False))
    need_review_count = sum(1 for r in records if r.get("need_review", False))
    auto_scored_count = sum(1 for r in records if r.get("auto_scored", False))
    
    # By chain_type
    chain_stats = defaultdict(lambda: {"reqs": 0, "tokens": 0, "reasoning": 0, "latency_sum": 0, "latency_cnt": 0, "success": 0, "review": 0, "auto": 0, "img_w": 0, "img_h": 0, "img_s": 0, "img_cnt": 0})
    for r in records:
        ct = r.get("chain_type", "unknown")
        cs = chain_stats[ct]
        cs["reqs"] += 1
        cs["tokens"] += (r.get("total_tokens", 0) or 0)
        cs["reasoning"] += (r.get("reasoning_tokens", 0) or 0)
        lat = r.get("latency_ms", 0) or 0
        if lat > 0:
            cs["latency_sum"] += lat
            cs["latency_cnt"] += 1
        if r.get("success", False): cs["success"] += 1
        if r.get("need_review", False): cs["review"] += 1
        if r.get("auto_scored", False): cs["auto"] += 1
        
        iw = r.get("image_width")
        ih = r.get("image_height")
        isz = r.get("image_file_size_kb")
        if iw is not None and ih is not None and isz is not None:
            cs["img_w"] += iw
            cs["img_h"] += ih
            cs["img_s"] += isz
            cs["img_cnt"] += 1

    # By question_type
    qt_stats = defaultdict(lambda: {"reqs": 0, "tokens": 0, "latency_sum": 0, "latency_cnt": 0, "review": 0})
    for r in records:
        qt = r.get("question_type", "unknown")
        if not qt: qt = "unknown"
        qs = qt_stats[qt]
        qs["reqs"] += 1
        qs["tokens"] += (r.get("total_tokens", 0) or 0)
        lat = r.get("latency_ms", 0) or 0
        if lat > 0:
            qs["latency_sum"] += lat
            qs["latency_cnt"] += 1
        if r.get("need_review", False): qs["review"] += 1
        
    # By model
    model_stats = defaultdict(lambda: {"reqs": 0, "tokens": 0, "reasoning": 0, "latency_sum": 0, "latency_cnt": 0})
    for r in records:
        m = r.get("actual_model_used", "unknown")
        if not m: m = "unknown"
        ms = model_stats[m]
        ms["reqs"] += 1
        ms["tokens"] += (r.get("total_tokens", 0) or 0)
        ms["reasoning"] += (r.get("reasoning_tokens", 0) or 0)
        lat = r.get("latency_ms", 0) or 0
        if lat > 0:
            ms["latency_sum"] += lat
            ms["latency_cnt"] += 1

    # Top 20 expensive
    sorted_records = sorted(records, key=lambda x: x.get("total_tokens", 0) or 0, reverse=True)[:20]

    if output_markdown:
        md_path = Path("logs/llm_usage_summary.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# LLM 使用量统计报告\n\n")
            f.write("## 一、总体统计\n")
            f.write(f"- 总请求数: {total_reqs}\n")
            f.write(f"- 总 prompt_tokens: {total_prompt}\n")
            f.write(f"- 总 cached_tokens: {total_cached}\n")
            f.write(f"- 总 completion_tokens: {total_comp}\n")
            f.write(f"- 总 reasoning_tokens: {total_reasoning}\n")
            f.write(f"- 总 total_tokens: {total_tokens}\n")
            f.write(f"- 缓存命中率 (cached/prompt): {total_cached/total_prompt*100:.2f}%" if total_prompt else "- 缓存命中率: 0%\n")
            f.write("\n")
            f.write(f"- reasoning 占比 (reasoning/total): {total_reasoning/total_tokens*100:.2f}%" if total_tokens else "- reasoning 占比: 0%\n")
            f.write("\n")
            f.write(f"- 平均 latency_ms: {avg_latency:.2f}\n")
            f.write(f"- 成功率: {success_count/total_reqs*100:.2f}%\n" if total_reqs else "- 成功率: 0%\n")
            f.write(f"- JSON 成功率: {json_valid_count/total_reqs*100:.2f}%\n" if total_reqs else "- JSON 成功率: 0%\n")
            f.write(f"- need_review 数量: {need_review_count}\n")
            f.write(f"- auto_scored 数量: {auto_scored_count}\n\n")
            
            f.write("## 二、按 chain_type 分组\n\n")
            f.write("| chain_type | 请求数 | 总 token | 平均 token | 总 reasoning | 平均 latency | 成功率 | need_review | auto_scored | 平均图宽 | 平均图高 | 平均大小(KB) |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|---|---|\n")
            for ct, cs in chain_stats.items():
                avg_tok = cs["tokens"] / cs["reqs"]
                avg_lat = cs["latency_sum"] / cs["latency_cnt"] if cs["latency_cnt"] > 0 else 0
                succ = cs["success"] / cs["reqs"] * 100
                avg_w = cs["img_w"] / cs["img_cnt"] if cs["img_cnt"] > 0 else 0
                avg_h = cs["img_h"] / cs["img_cnt"] if cs["img_cnt"] > 0 else 0
                avg_s = cs["img_s"] / cs["img_cnt"] if cs["img_cnt"] > 0 else 0
                f.write(f"| {ct} | {cs['reqs']} | {cs['tokens']} | {avg_tok:.1f} | {cs['reasoning']} | {avg_lat:.1f} | {succ:.1f}% | {cs['review']} | {cs['auto']} | {avg_w:.1f} | {avg_h:.1f} | {avg_s:.1f} |\n")
            f.write("\n")
            
            f.write("## 三、按 question_type 分组\n\n")
            f.write("| question_type | 请求数 | 平均 token | 平均耗时 | need_review 率 |\n")
            f.write("|---|---|---|---|---|\n")
            for qt, qs in qt_stats.items():
                avg_tok = qs["tokens"] / qs["reqs"]
                avg_lat = qs["latency_sum"] / qs["latency_cnt"] if qs["latency_cnt"] > 0 else 0
                rev_rate = qs["review"] / qs["reqs"] * 100
                f.write(f"| {qt} | {qs['reqs']} | {avg_tok:.1f} | {avg_lat:.1f} | {rev_rate:.1f}% |\n")
            f.write("\n")
            
            f.write("## 四、按模型分组\n\n")
            f.write("| actual_model_used | 请求数 | 总 token | reasoning_tokens | 平均 latency_ms |\n")
            f.write("|---|---|---|---|---|\n")
            for m, ms in model_stats.items():
                avg_lat = ms["latency_sum"] / ms["latency_cnt"] if ms["latency_cnt"] > 0 else 0
                f.write(f"| {m} | {ms['reqs']} | {ms['tokens']} | {ms['reasoning']} | {avg_lat:.1f} |\n")
            f.write("\n")
            
            f.write("## 五、最贵请求 Top 20\n\n")
            f.write("| timestamp | student_id | question_id | chain_type | actual_model_used | total_tokens | reasoning_tokens | latency_ms | success | error_type |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|\n")
            for r in sorted_records:
                f.write(f"| {r.get('timestamp')} | {r.get('student_id')} | {r.get('question_id')} | {r.get('chain_type')} | {r.get('actual_model_used')} | {r.get('total_tokens')} | {r.get('reasoning_tokens')} | {r.get('latency_ms')} | {r.get('success')} | {r.get('error_type')} |\n")
                
        print(f"Exported markdown report to {md_path}")
        
    if output_csv:
        csv_path = Path("logs/llm_usage_summary.csv")
        if records:
            keys = list(records[0].keys())
            # Add missing keys if they appear in other records
            for r in records:
                for k in r.keys():
                    if k not in keys:
                        keys.append(k)
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                for r in records:
                    writer.writerow(r)
        print(f"Exported csv to {csv_path}")

    return {"status": "success", "total_records": total_reqs}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=str, default="logs/llm_usage.jsonl")
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    
    if args.export:
        summarize_usage(args.log)
    else:
        print("Run with --export to generate reports.")
