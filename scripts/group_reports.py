"""关键研报分组：按财报窗口轮转分配到 N 组，输出 analysis/{code}/_groups/group_{A..}.json
供精读子代理并行抽取（核心逻辑/风险/EPS预测/目标价）。

用法: python3 scripts/group_reports.py --code 300308 [--groups 6]
"""

import json
import os
import sys
from argparse import ArgumentParser

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import log  # noqa: E402


def main():
    ap = ArgumentParser(description="关键研报分组")
    ap.add_argument("--code", required=True)
    ap.add_argument("--groups", type=int, default=6)
    args = ap.parse_args()

    code = args.code
    reports = json.load(open(os.path.join(_REPO, "data", code, "reports.json"), encoding="utf-8"))
    keys = [r for r in reports["reports"] if r.get("key") and r.get("txt_file")]
    by_w = {}
    for r in keys:
        for w in r.get("windows", [r.get("window")]):
            by_w.setdefault(w, {})[r["infoCode"]] = r
    wins = sorted(by_w)
    letters = [chr(ord("A") + i) for i in range(args.groups)]
    groups = {g: [] for g in letters}
    for i, w in enumerate(wins):
        groups[letters[i % args.groups]].append(w)
    out_dir = os.path.join(_REPO, "analysis", code, "_groups")
    os.makedirs(out_dir, exist_ok=True)
    for g, ws in groups.items():
        payload = {}
        for w in ws:
            payload[w] = [{"infoCode": ic, "org": r["org"], "date": r["publish_date"],
                           "title": r["title"], "rating": r["rating"],
                           "src_url": r["source_url"],
                           "txt": os.path.join("data", code, r["txt_file"])}
                          for ic, r in by_w[w].items()]
        path = os.path.join(out_dir, f"group_{g}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        n = sum(len(v) for v in payload.values())
        log(f"group_{g}: {len(ws)} 个窗口 {ws[:3]}{'...' if len(ws) > 3 else ''} ，{n} 条（含跨窗口重复）")
    uniq = len({r["infoCode"] for r in keys})
    log(f"共 {uniq} 篇唯一关键研报，分 {args.groups} 组 → {out_dir}")


if __name__ == "__main__":
    main()
