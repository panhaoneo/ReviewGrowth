"""抓取机构研报：全量元数据（评级/EPS预测）+ 每财报窗口关键研报 PDF 精读文本

输出: data/{code}/reports.json + data/{code}/reports_txt/{infoCode}.txt（PDF 存 reports_pdf/，不入库）
依赖: 先运行 fetch_earnings（窗口基于财报披露日）。
"""

import os
import re
import sys
import time
from datetime import date, timedelta

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import (cfg_argparse, data_dir, is_fresh, load_config, load_json,  # noqa: E402
                    log, now_str, save_json)
from em_api import download_pdf, eastmoney_reports, report_url  # noqa: E402

_TITLE_KW = ["业绩", "超预期", "不及预期", "上调", "下调", "点评", "中报", "季报", "年报", "深度"]
_STRONG_RATING = ["买入", "强推", "强烈推荐"]


def parse_eps(v):
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def extract_text(pdf_path, max_pages=3):
    """pypdf 抽前 N 页（研报前 3 页含核心观点/盈利预测/风险提示）"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        parts = []
        for i, page in enumerate(reader.pages[:max_pages]):
            t = page.extract_text() or ""
            parts.append(f"\n===== 第{i + 1}页 =====\n{t}")
        text = "".join(parts)
        return text if len(text.strip()) > 50 else None
    except Exception:
        return None


def select_key_reports(reports, windows, per_window):
    """确定性选稿：score = 40*标题命中 + min(20, 2*|EPS修订%|) + 30*窗口内机构首篇
    + 10*强评级 - 0.5*距公告日天数；按 score 降序机构去重取前 N。"""
    picked = []
    for period, report_date in windows:
        d0 = date.fromisoformat(report_date)
        lo, hi = d0 - timedelta(days=5), d0 + timedelta(days=15)
        cands = [r for r in reports
                 if r["publish_date"] and lo.isoformat() <= r["publish_date"] <= hi.isoformat()]
        if not cands:
            continue
        cands.sort(key=lambda r: r["publish_date"])
        first_by_org = {}
        for r in cands:
            first_by_org.setdefault(r["org"], r["infoCode"])
        # 同机构上一篇（按发布时间）用于计算 EPS 修订
        by_org = {}
        for r in sorted(reports, key=lambda x: x["publish_date"]):
            by_org.setdefault(r["org"], []).append(r)

        def revision_pct(r):
            hist = [x for x in by_org.get(r["org"], [])
                    if x["publish_date"] < r["publish_date"] and x["eps_this"]]
            if not hist or not r["eps_this"]:
                return 0.0
            prev = parse_eps(hist[-1]["eps_this"])
            cur = parse_eps(r["eps_this"])
            if not prev or not cur:
                return 0.0
            return (cur / prev - 1) * 100

        scored = []
        for r in cands:
            kw = sum(1 for k in _TITLE_KW if k in r["title"])
            rev = revision_pct(r)
            days = (date.fromisoformat(r["publish_date"]) - d0).days
            rating_bonus = 10 if any(s in (r["rating"] or "") for s in _STRONG_RATING) else 0
            score = (40 * min(kw, 1) + min(20, 2 * abs(rev))
                     + (30 if first_by_org.get(r["org"]) == r["infoCode"] else 0)
                     + rating_bonus - 0.5 * max(days, 0))
            scored.append({**r, "score": round(score, 1),
                           "changes": {"eps_this_revision_pct": round(rev, 1) if rev else 0.0,
                                       "first_in_window": first_by_org.get(r["org"]) == r["infoCode"]}})
        scored.sort(key=lambda x: (-x["score"], x["publish_date"]))
        seen_org, window_pick = set(), []
        for r in scored:
            if r["org"] in seen_org:
                continue
            seen_org.add(r["org"])
            window_pick.append(r)
            if len(window_pick) >= per_window:
                break
        for r in window_pick:
            picked.append({**r, "key": True, "window": period})
    return picked


def main():
    ap = cfg_argparse("抓取研报元数据与关键PDF")
    args = ap.parse_args()
    cfg = load_config(args.code)
    code = cfg["code"]
    out_path = os.path.join(data_dir(code), "reports.json")
    if not args.force and is_fresh(out_path, cfg["refresh_days"]):
        log("reports.json 新鲜，跳过")
        return

    ddir = data_dir(code)
    earn = load_json(os.path.join(ddir, "earnings.json")) or {}
    if not earn.get("periods"):
        raise RuntimeError("earnings.json 缺失，先运行 fetch_earnings.py")

    begin = cfg.get("warmup_start") or cfg["start_date"]
    end = (date.fromisoformat(cfg["end_date"]) + timedelta(days=120)).isoformat()
    log(f"东财研报元数据 {begin} ~ {end}")
    reports = eastmoney_reports(code, begin=begin, end=end)
    reports = [r for r in reports if r["publish_date"] and r["infoCode"]]
    log(f"研报 {len(reports)} 篇")

    windows = [(p["period"], p["report_date"]) for p in earn["periods"] if p.get("report_date")]
    picked = select_key_reports(reports, windows, cfg["pdf_per_window"])
    log(f"关键研报选定 {len(picked)} 篇（{len(windows)} 个财报窗口，每窗口 ≤{cfg['pdf_per_window']}）")

    pdf_dir = os.path.join(ddir, "reports_pdf")
    txt_dir = os.path.join(ddir, "reports_txt")
    os.makedirs(txt_dir, exist_ok=True)
    n_pdf, n_txt = 0, 0
    for r in picked:
        path = download_pdf(r["infoCode"], r["publish_date"], r["org"], r["title"], pdf_dir)
        time.sleep(0.5)   # pdf.dfcfw.com 批量限流缓解
        r["pdf_ok"] = bool(path)
        if path:
            n_pdf += 1
            txt_rel = os.path.join("reports_txt", f"{r['infoCode']}.txt")
            txt_abs = os.path.join(ddir, txt_rel)
            if os.path.exists(txt_abs) and os.path.getsize(txt_abs) > 200:
                r["txt_file"] = txt_rel
                n_txt += 1
                continue
            text = extract_text(path)
            if text:
                header = (f"标题: {r['title']}\n机构: {r['org']} | 分析师: {r['analyst']}\n"
                          f"日期: {r['publish_date']} | 评级: {r['rating']}\n"
                          f"EPS预测(当年/次年/后年): {r['eps_this']} / {r['eps_next']} / {r['eps_next2']}\n"
                          f"来源: {report_url(r['infoCode'])}\n"
                          f"窗口: {r['window']} | score={r['score']}\n")
                with open(txt_abs, "w", encoding="utf-8") as f:
                    f.write(header + text)
                r["txt_file"] = txt_rel
                n_txt += 1
            else:
                r["txt_file"] = None
        else:
            r["txt_file"] = None
    log(f"PDF 下载 {n_pdf}/{len(picked)}，文本可用 {n_txt}")

    # 一致预期散点（每篇研报的当年/次年/后年 EPS）
    consensus_points = []
    for r in reports:
        if not r["publish_date"]:
            continue
        y = int(r["publish_date"][:4])
        for fy, eps in ((y, r["eps_this"]), (y + 1, r["eps_next"]), (y + 2, r["eps_next2"])):
            e = parse_eps(eps)
            if e:
                consensus_points.append({"d": r["publish_date"], "fy": fy, "eps": e,
                                         "org": r["org"], "infoCode": r["infoCode"]})

    # 合并：记录该报告被哪些窗口选中（重叠窗口如年报/一季报同周披露）
    picked_by = {}
    for p in picked:
        picked_by.setdefault(p["infoCode"], []).append(p)
    merged = []
    for r in reports:
        rr = dict(r)
        ps = picked_by.get(r["infoCode"], [])
        rr["key"] = bool(ps)
        rr["windows"] = [p["window"] for p in ps]
        rr["source_url"] = report_url(r["infoCode"])
        if ps:
            kp = ps[0]
            rr.update({k: kp[k] for k in ("score", "changes", "window", "pdf_ok", "txt_file")})
        merged.append(rr)

    save_json(out_path, {
        "fetch_time": now_str(),
        "source": "东方财富 reportapi（qType=0）+ PDF pdf.dfcfw.com",
        "reports": merged,
        "consensus_points": consensus_points,
        "key_selection_rule": "每财报窗口[-5d,+15d] 按 score 降序机构去重取前N：40*标题命中+min(20,2*|EPS修订%|)+30*机构首篇+10*强评级-0.5*距公告日天数",
        "gaps": [{"scope": f"关键研报PDF {len(picked) - n_pdf} 篇未下载成功",
                  "reason": "东财 PDF 404/网络", "impact": "该窗口研报仅元数据"}] if n_pdf < len(picked) else [],
    })
    log(f"reports.json: {len(merged)} 篇（关键 {len(picked)}，consensus 散点 {len(consensus_points)}）")


if __name__ == "__main__":
    main()
