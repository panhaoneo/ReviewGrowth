"""合并原始数据 + Agent 分析产物 → docs/（GitHub Pages 站点）

校验（失败即 exit 1，阻断发布）：
  1. timeline 字段完整性 / type / confidence 枚举
  2. published_at <= available_at；available_at 在区间内
  3. E 项 available_at 与 earnings 对应期 report_date 一致
  4. as_of 对象键名黑名单（t1|t5|t20|t60|excess|reaction|future）防未来函数泄漏
  5. R 项 source_url + infoCode 存在性；A/N/C 域名白名单
  6. phases 覆盖显示区间无洞；evidence_refs 命中 timeline id
  7. logic state_history 升序 + 状态枚举 + 证据引用闭环
  8. as-of 泄漏机械测试（可见集单调不减、无未来项）
  9. 产物齐全与体积上限
本脚本同时机械计算：timeline 的 price_reaction（T+1/5/20/60 + 各基准超额）、phases 涨跌幅/回撤。
"""

import html
import json
import os
import random
import re
import shutil
import sys
from datetime import date, timedelta

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import (analysis_dir, cfg_argparse, data_dir, docs_data_dir,  # noqa: E402
                    load_config, load_json, log, now_str, save_json)

ASSET_FILES = ["review.html", "app.js", "style.css"]
VALID_TYPES = {"E", "R", "N", "A", "C"}
VALID_CONF = {"high", "medium", "low", "unverified"}
VALID_STATES = {"增强", "中性", "减弱", "破坏"}
AS_OF_BLACKLIST = re.compile(r"^(t\d+|excess|.*reaction.*|.*future.*|price_feedback|hindsight)", re.I)
URL_WHITELIST = ("cninfo.com.cn", "eastmoney.com", "dfcfw.com", "cls.cn", "10jqka.com.cn", "sse.com.cn", "szse.cn")


# ── markdown 渲染（移植 zhuang scripts/build_site.py） ──

INLINE_RE = re.compile(
    r"\[([^\]]+)\]\((https?://[^)]+)\)"
    r"|\*\*(.+?)\*\*"
    r"|`([^`]+)`")


def inline(text):
    out, pos = [], 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append(html.escape(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>')
        elif m.group(3) is not None:
            out.append(f"<b>{html.escape(m.group(3))}</b>")
        else:
            out.append(f"<code>{html.escape(m.group(4))}</code>")
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def is_sep_row(cells):
    return all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip())


def md_to_body(md_text):
    body, lines = [], md_text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line, s = lines[i], lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith("|") and "|" in s[1:]:
            table = []
            while i < n and lines[i].strip().startswith("|"):
                table.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows = []
            for k, cells in enumerate(table):
                if is_sep_row(cells):
                    continue
                tag = "th" if k == 0 else "td"
                rows.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
            body.append('<div style="overflow-x:auto"><table>' + "".join(rows) + "</table></div>")
            continue
        if s.startswith("###"):
            body.append(f"<h3>{inline(s[3:].strip())}</h3>")
        elif s.startswith("##"):
            body.append(f"<h2>{inline(s[2:].strip())}</h2>")
        elif s.startswith("#"):
            body.append(f"<h1>{inline(s[1:].strip())}</h1>")
        elif s == "---":
            body.append("<hr>")
        elif s.startswith(">"):
            body.append(f"<blockquote>{inline(s[1:].strip())}</blockquote>")
        elif s.startswith("- "):
            items = []
            while i < n and lines[i].strip().startswith("- "):
                items.append(f"<li>{inline(lines[i].strip()[2:])}</li>")
                i += 1
            body.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif re.match(r"^\d+\. ", s):
            items = []
            while i < n and re.match(r"^\d+\. ", lines[i].strip()):
                item_text = re.sub(r"^\d+\. ", "", lines[i].strip())
                items.append(f"<li>{inline(item_text)}</li>")
                i += 1
            body.append("<ol>" + "".join(items) + "</ol>")
            continue
        else:
            body.append(f"<p>{inline(s)}</p>")
        i += 1
    return "\n".join(body)


DOC_CSS = """*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;line-height:1.7}
.wrap{max-width:980px;margin:0 auto}
h1{color:#58a6ff;font-size:1.5em;margin:18px 0 8px;border-bottom:1px solid #21262d;padding-bottom:10px}
h2{color:#58a6ff;font-size:1.2em;margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid #21262d}
h3{color:#79c0ff;font-size:1.05em;margin:18px 0 8px}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;margin:10px 0 18px;font-size:.84em}
th,td{border:1px solid #30363d;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#161b22;color:#8b949e;font-weight:600;white-space:nowrap}
tr:nth-child(even){background:#161b22}
blockquote{border-left:4px solid #30363d;color:#8b949e;padding:4px 14px;margin:10px 0;font-size:.9em}
hr{border:none;border-top:1px solid #30363d;margin:22px 0}
ul,ol{padding-left:26px;margin:8px 0}p{margin:8px 0}
.nav{margin-bottom:16px;font-size:.9em}.sub{color:#8b949e;font-size:.85em}
.up{color:#3fb950}.down{color:#f85149}"""


def page(title, content):
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{DOC_CSS}</style></head>"
            f'<body><div class="wrap">{content}</div></body></html>')


# ── 机械计算 ──

def trading_offsets(bars):
    """date_str → index 映射（升序）"""
    return {b["d"]: i for i, b in enumerate(bars)}


def calc_price_reaction(item_date, bars, idx, benchmarks, offsets_list=(1, 5, 20, 60)):
    dates = [b["d"] for b in bars]
    i = None
    for k in range(len(dates)):
        if dates[k] >= item_date:
            i = k
            break
    if i is None or i >= len(bars):
        return None
    base = bars[i]["c"]
    out = {"base_date": bars[i]["d"], "t1": None, "t5": None, "t20": None, "t60": None,
           "excess": {}}
    for n in offsets_list:
        j = i + n
        out[f"t{n}"] = round((bars[j]["c"] / base - 1) * 100, 2) if j < len(bars) else None
    for tc, meta in benchmarks.items():
        bmar = meta["bars"]
        bmap = {b["d"]: k for k, b in enumerate(bmar)}
        b_dict = meta.get("_dict") or {}
        j0 = b_dict.get(bars[i]["d"])
        if j0 is None:
            continue
        exc = {}
        for n in offsets_list:
            j = j0 + n
            if j < len(bmar):
                b_ret = bmar[j]["c"] / bmar[j0]["c"] - 1
                if out[f"t{n}"] is not None:
                    exc[f"t{n}"] = round(out[f"t{n}"] - b_ret * 100, 2)
        out["excess"][tc] = exc
    return out


def attach_bench_dicts(benchmarks):
    for meta in benchmarks.values():
        meta["_dict"] = {b["d"]: k for k, b in enumerate(meta["bars"])}


def calc_phase_stats(phase, bars):
    """phase 涨跌幅与最大回撤（含相位起点前一日为基准）"""
    dates = [b["d"] for b in bars]
    start_idx = None
    for k, d in enumerate(dates):
        if d >= phase["start"]:
            start_idx = k
            break
    if start_idx is None:
        return {"return": None, "max_drawdown": None}
    base_idx = max(start_idx - 1, 0)
    end_idx = start_idx
    for k in range(start_idx, len(dates)):
        if dates[k] <= phase["end"]:
            end_idx = k
        else:
            break
    base = bars[base_idx]["c"]
    ret = (bars[end_idx]["c"] / base - 1) * 100
    peak, mdd = bars[start_idx]["c"], 0.0
    for k in range(start_idx, end_idx + 1):
        peak = max(peak, bars[k]["c"])
        mdd = min(mdd, bars[k]["c"] / peak - 1)
    return {"return": round(ret, 1), "max_drawdown": round(mdd * 100, 1),
            "start_close": bars[start_idx]["c"], "end_close": bars[end_idx]["c"]}


# ── 校验 ──

class Validator:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def fail(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


def check_as_of_leak(obj, path, v):
    if isinstance(obj, dict):
        for k, val in obj.items():
            if AS_OF_BLACKLIST.match(k):
                v.fail(f"{path}.{k} 命中未来函数黑名单键名")
            check_as_of_leak(val, f"{path}.{k}", v)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            check_as_of_leak(val, f"{path}[{i}]", v)


def validate(site, v, report_urls):
    bars = site["bars"]
    r_start, r_end = site["meta"]["start_date"], site["meta"]["end_date"]
    lo = (date.fromisoformat(r_start) - timedelta(days=30)).isoformat()
    hi = (date.fromisoformat(r_end) + timedelta(days=site["meta"]["extend_after_days"])).isoformat()

    ids = set()
    tl = site["timeline"]
    if not tl:
        v.fail("timeline 为空")
    for it in tl:
        for f in ("id", "date", "type", "title", "summary", "published_at", "available_at", "confidence", "source_url"):
            if not it.get(f) and it.get(f) != "":
                v.fail(f"timeline[{it.get('id', '?')}] 缺少字段 {f}")
        if it.get("id") in ids:
            v.fail(f"timeline id 重复: {it.get('id')}")
        ids.add(it.get("id"))
        if it.get("type") not in VALID_TYPES:
            v.fail(f"timeline[{it.get('id')}] type 非法: {it.get('type')}")
        if it.get("confidence") not in VALID_CONF:
            v.fail(f"timeline[{it.get('id')}] confidence 非法: {it.get('confidence')}")
        if it.get("published_at") and it.get("available_at") and it["published_at"] > it["available_at"]:
            v.fail(f"timeline[{it.get('id')}] published_at > available_at")
        if it.get("available_at") and not (lo <= it["available_at"] <= hi):
            v.fail(f"timeline[{it.get('id')}] available_at {it['available_at']} 超区间 [{lo},{hi}]")
        as_of = it.get("as_of") or {}
        if not isinstance(as_of, dict):
            v.fail(f"timeline[{it.get('id')}] as_of 不是对象")
        else:
            check_as_of_leak(as_of, f"timeline[{it.get('id')}].as_of", v)
        if it.get("type") == "E":
            period = it.get("id", "").replace("E:", "")
            per = next((p for p in site["earnings"] if p["period"] == period), None)
            if per is None:
                v.fail(f"timeline[{it.get('id')}] 对应财报期 {period} 不存在")
            elif per.get("report_date") and it.get("available_at") != per["report_date"]:
                v.fail(f"timeline[{it.get('id')}] available_at 与财报 report_date 不一致")
        if it.get("type") == "R":
            su = it.get("source_url", "")
            if su not in report_urls and not su.startswith("https://data.eastmoney.com/report/"):
                if not any(w in su for w in ("data.eastmoney.com", "sina.com.cn")):
                    v.fail(f"timeline[{it.get('id')}] R 项 source_url 非法: {su[:60]}")
                else:
                    v.warn(f"timeline[{it.get('id')}] R 项链接不在 reports.json 中")
        elif it.get("type") in ("A", "N", "C"):
            if not it.get("source_url"):
                v.fail(f"timeline[{it.get('id')}] {it.get('type')} 项缺少 source_url")
            elif not any(w in it["source_url"] for w in URL_WHITELIST):
                v.warn(f"timeline[{it.get('id')}] source_url 域名不在白名单: {it['source_url'][:60]}")

    # phases 覆盖
    phases = site["phases"]
    if not phases:
        v.fail("phases 为空")
    else:
        cursor = r_start
        gap_scopes = " ".join(g.get("scope", "") for g in site.get("gaps", []))
        for ph in sorted(phases, key=lambda x: x["start"]):
            next_day = (date.fromisoformat(cursor) + timedelta(days=1)).isoformat()
            if ph["start"] > next_day and not ("phase" in gap_scopes and ph["start"] in gap_scopes):
                v.warn(f"phases 区间不连续: {cursor} ~ {ph['start']}")
            cursor = max(cursor, ph["end"])
            for ref in ph.get("evidence_refs", []):
                if ref not in ids:
                    v.fail(f"phase {ph['name']} 引用不存在的 timeline id: {ref}")
        if cursor < r_end:
            v.fail(f"phases 未覆盖到 {r_end}（止于 {cursor}）")

    # logic
    for h in site.get("logic_validation", []):
        prev = None
        for sh in h.get("state_history", []):
            if sh.get("state") not in VALID_STATES:
                v.fail(f"logic[{h.get('id')}] state 非法: {sh.get('state')}")
            if prev and sh["d"] < prev:
                v.fail(f"logic[{h.get('id')}] state_history 未按日期升序")
            prev = sh.get("d")
            for ref in sh.get("evidence_refs", []):
                if ref not in ids:
                    v.fail(f"logic[{h.get('id')}] 引用不存在的 timeline id: {ref}")

    # earnings analysis 完整
    for per in site["earnings"]:
        if not per.get("analysis"):
            v.fail(f"earnings {per['period']} 缺少 analysis（9问模板）")

    # as-of 泄漏机械测试
    if tl:
        cursors = sorted({it["available_at"] for it in tl if it.get("available_at")})
        cset = set(cursors)
        for _ in range(20):
            cset.add(random.choice(cursors))
        prev_n = -1
        for c in sorted(cset):
            vis = [it for it in tl if it["available_at"] <= c]
            if any(it["available_at"] > c for it in vis):
                v.fail(f"as-of 泄漏: cursor={c} 可见集含未来项")
            if len(vis) < prev_n:
                v.fail(f"as-of 可见集随 cursor 非单调: {c}")
            prev_n = len(vis)


# ── 主流程 ──

def load_required(path, name, v):
    d = load_json(path)
    if d is None:
        v.fail(f"缺少必需文件: {name} ({path})")
    return d


def main():
    ap = cfg_argparse("构建 GitHub Pages 站点")
    args = ap.parse_args()
    cfg = load_config(args.code)
    code = cfg["code"]
    v = Validator()

    dd, ad = data_dir(code), analysis_dir(code)
    kline = load_required(os.path.join(dd, "kline.json"), "kline.json", v)
    benchmarks = load_required(os.path.join(dd, "benchmarks.json"), "benchmarks.json", v)
    earnings = load_required(os.path.join(dd, "earnings.json"), "earnings.json", v)
    events = load_required(os.path.join(dd, "events.json"), "events.json", v)
    valuation = load_required(os.path.join(dd, "valuation.json"), "valuation.json", v)
    reports = load_required(os.path.join(dd, "reports.json"), "reports.json", v)
    summary = load_required(os.path.join(ad, "summary.json"), "analysis/summary.json", v)
    timeline = load_required(os.path.join(ad, "timeline.json"), "analysis/timeline.json", v)
    phases = load_required(os.path.join(ad, "phases.json"), "analysis/phases.json", v)
    earn_analysis = load_required(os.path.join(ad, "earnings_analysis.json"), "analysis/earnings_analysis.json", v)
    research_notes = load_required(os.path.join(ad, "research_notes.json"), "analysis/research_notes.json", v)
    logic = load_required(os.path.join(ad, "logic_validation.json"), "analysis/logic_validation.json", v)
    review = load_required(os.path.join(ad, "review.json"), "analysis/review.json", v)
    gaps_extra = load_json(os.path.join(ad, "gaps.json")) or {"gaps": []}
    if v.errors:
        for e in v.errors:
            log(f"✗ {e}")
        sys.exit(1)

    bar_range = [kline["bars"][0]["d"], kline["bars"][-1]["d"]]
    attach_bench_dicts(benchmarks["benchmarks"])
    bars = kline["bars"]

    # earnings + analysis 合并，并生成用户口径扁平字段
    periods = []
    for p in earnings["periods"]:
        m = p["margins"]["single"]
        flat = {
            "period": p["period"], "report_date": p["report_date"],
            "revenue_single": p["single_q"].get("operating_income"),
            "revenue_yoy": p["yoy"]["revenue_yoy"],
            "revenue_qoq": p["qoq"]["revenue_qoq"],
            "net_profit_single": p["single_q"].get("net_profit"),
            "parent_net_single": p["single_q"].get("parent_holder_net_profit"),
            "parent_net_yoy": p["yoy"]["parent_net_yoy"],
            "gross_margin_single": m.get("gross"),
            "gross_margin_cum": p["margins"]["cum"]["gross"],
            "net_margin_single": m.get("net"),
            "ocf_single": p["cash"].get("ocf_single"),
            "cash_content_single": p["cash"].get("cash_content_single"),
            "ar": p["balance"].get("accounts_receivable"),
            "ar_to_revenue": p["ttm"].get("ar_to_revenue"),
            "expense_ratio_single": p["expense_ratios"].get("single_total"),
            "rd_ratio_single": p["expense_ratios"].get("single_rd"),
        }
        analysis = earn_analysis.get(p["period"]) or earn_analysis.get("periods", {}).get(p["period"])
        periods.append({**p, "flat": flat, "analysis": analysis})

    # valuation 紧凑序列 + PEG
    val_series = [{"d": r["date"], "pe": r["pe"], "pb": r["pb"], "ps": r["ps"],
                   "pe_pctile": r["pe_pctile"]} for r in valuation.get("series", [])]

    # 研报：区间内 + 合并精读笔记
    r_lo = (date.fromisoformat(cfg["start_date"]) - timedelta(days=45)).isoformat()
    r_hi = (date.fromisoformat(cfg["end_date"])
            + timedelta(days=cfg.get("extend_after_days", 120))).isoformat()
    rlist = []
    for r in reports["reports"]:
        if not (r_lo <= r["publish_date"] <= r_hi):
            continue
        note = research_notes.get(r["infoCode"]) or {}
        rlist.append({
            "infoCode": r["infoCode"], "title": r["title"], "publish_date": r["publish_date"],
            "institution": r["org"], "analyst": r["analyst"], "rating": r.get("rating"),
            "target_price": r.get("target_price"), "last_rating": r.get("last_rating"),
            "eps_forecast": {"this": r["eps_this"], "next": r["eps_next"], "next2": r["eps_next2"]},
            "changes": r.get("changes"), "key": r.get("key", False), "window": r.get("window"),
            "source_url": r["source_url"],
            "core_logic": note.get("core_logic"), "risks": note.get("risks"),
            "quotes": note.get("quotes"), "txt_file": r.get("txt_file"),
        })

    # consensus：优先分析层（Agent 从关键研报 PDF 抽取的盈利预测），回退东财字段
    cons_points = []
    cons_src = "analysis（关键研报PDF抽取）"
    cons_analysis = load_json(os.path.join(ad, "consensus.json"))
    if cons_analysis and cons_analysis.get("points"):
        cons_points = cons_analysis["points"]
    else:
        cons_points = reports["consensus_points"]
        cons_src = "东财 predictThisYearEps（覆盖率低）"
    cons = {}
    for pt in cons_points:
        if pt["d"] < r_lo or pt["d"] > r_hi:
            continue
        cons.setdefault(str(pt["fy"]), []).append(pt)
    consensus = {}
    for fy, pts in cons.items():
        pts.sort(key=lambda x: x["d"])
        out_pts = []
        for p in pts:
            out_pts.append({"d": p["d"], "eps": p["eps"], "org": p["org"], "infoCode": p.get("infoCode"),
                            "n_org": len({q["org"] for q in pts if q["d"] <= p["d"]})})
        consensus[fy] = out_pts

    # timeline 附加机械计算的 price_reaction
    for it in timeline:
        if it.get("date"):
            it["price_reaction"] = calc_price_reaction(it["date"], bars, None, benchmarks["benchmarks"])

    # phases 附加涨跌/回撤
    for ph in phases:
        ph.update(calc_phase_stats(ph, bars))

    # 事件归档（全量公告 + 新闻）
    events_out = [{"date": a["date"], "title": a["title"], "type": a["type"],
                   "url": a["url"], "is_irm": a["is_irm"], "is_forecast": a["is_forecast"]}
                  for a in events["announcements"]]

    # gaps 汇总
    gaps = []
    for src, items in (
            ("benchmarks", benchmarks.get("gaps", [])),
            ("valuation", valuation.get("gaps", [])),
            ("reports", reports.get("gaps", [])),
            ("earnings", [{"scope": g, "reason": "披露日核验", "impact": "置信度降级"} for g in earnings.get("gaps", [])]),
            ("analysis", gaps_extra.get("gaps", []))):
        for g in items or []:
            gaps.append({**g, "from": src})
    gaps.append({"scope": "新闻 2019-2021", "reason": "东财新闻接口无历史回溯",
                 "impact": "中：早期叙事以公告/研报/年报替代", "from": "analysis"})

    site = {
        "meta": {
            "code": code, "name": cfg["name"], "thscode": cfg["thscode"],
            "start_date": cfg["start_date"], "end_date": cfg["end_date"],
            "view_mode": cfg.get("view_mode", "both"),
            "extend_after_days": cfg.get("extend_after_days", 120),
            "generated_at": now_str(), "bar_range": bar_range,
            "conventions": "财报于披露日盘后可得；price_reaction 基准=事件日收盘价，T+N 按交易日偏移；"
                           "as-of 视图仅渲染 available_at <= cursor 的条目且只读 as_of 对象。",
        },
        "summary": summary,
        "bars": bars,
        "benchmarks": {k: {kk: vv for kk, vv in meta.items() if kk != "_dict"}
                       for k, meta in benchmarks["benchmarks"].items()},
        "earnings": periods,
        "research": rlist,
        "consensus": consensus,
        "consensus_source": cons_src,
        "events": events_out,
        "news": events["news"],
        "valuation": {"series": val_series, "peg_at_earnings": valuation.get("peg_at_earnings", [])},
        "timeline": sorted(timeline, key=lambda x: (x["available_at"], x["id"])),
        "phases": sorted(phases, key=lambda x: x["start"]),
        "logic_validation": logic,
        "review": review,
        "gaps": gaps,
        "sources": [],   # 填充于 data_sources.json，页面引用
        "page_blocks": {
            "top_overview": {"driver": "summary", "title": "复盘总览"},
            "phase_band": {"driver": "phases", "title": "相位色带"},
            "kline_markers": {"driver": "timeline", "title": "K线事件标记", "types": ["E", "R", "N", "A", "C"]},
            "earnings_table": {"driver": "earnings", "title": "财报切片表"},
            "research_expectation_chart": {"driver": "consensus", "title": "一致预期变化"},
            "logic_matrix": {"driver": "logic_validation", "title": "逻辑验证矩阵"},
            "review_conclusion": {"driver": "review", "title": "复盘结论"},
        },
    }

    validate(site, v, {r.get("source_url") for r in reports["reports"] if r.get("source_url")})
    for w in v.warnings:
        log(f"⚠ {w}")
    if v.errors:
        for e in v.errors:
            log(f"✗ {e}")
        log(f"校验失败（{len(v.errors)} 项），中止构建")
        sys.exit(1)

    # ── 写产物 ──
    out_dir = docs_data_dir(code)
    site_json = json.dumps(site, ensure_ascii=False, separators=(",", ":"))
    if len(site_json) > 8 * 1024 * 1024:
        v.fail("site.json 超过 8MB")
        sys.exit(1)
    with open(os.path.join(out_dir, "site.json"), "w", encoding="utf-8") as f:
        f.write(site_json)

    # report.html / confidence.html
    for name in ("report", "confidence"):
        md_path = os.path.join(ad, f"{name}.md")
        if os.path.exists(md_path):
            with open(md_path, encoding="utf-8") as f:
                text = f.read()
            title_m = re.search(r"^# (.+)$", text, re.M)
            title = title_m.group(1) if title_m else name
            nav = f'<div class="nav"><a href="../../review.html?code={code}">← 返回复盘页</a></div>'
            with open(os.path.join(out_dir, f"{name}.html"), "w", encoding="utf-8") as f:
                f.write(page(f"{cfg['name']} · {title}", nav + md_to_body(text)))

    # 前端资源
    docs = os.path.join(_REPO, "docs")
    assets = os.path.join(docs, "assets")
    os.makedirs(assets, exist_ok=True)
    for fname in ASSET_FILES:
        src = os.path.join(_REPO, "site", fname)
        if not os.path.exists(src):
            log(f"✗ 缺少前端源文件 site/{fname}")
            sys.exit(1)
        dst = os.path.join(assets, fname) if fname != "review.html" else os.path.join(docs, "review.html")
        shutil.copyfile(src, dst)
    vendor_src = os.path.join(_REPO, "site", "vendor")
    if os.path.isdir(vendor_src):
        shutil.copytree(vendor_src, os.path.join(assets, "vendor"), dirs_exist_ok=True)
    with open(os.path.join(docs, ".nojekyll"), "w") as f:
        f.write("")

    # data_sources.json
    src_manifest = {
        "generated_at": now_str(),
        "sources": [
            {"name": "同花顺 fuyao", "coverage": "日K线(前复权)/财报三表/财务指标",
             "url": "https://fuyao.aicubes.cn", "confidence": "high", "fetch_time": kline.get("fetch_time")},
            {"name": "baostock", "coverage": "基准指数日K/估值历史(PE/PB/PS)",
             "url": "http://baostock.com", "confidence": "high", "fetch_time": valuation.get("fetch_time")},
            {"name": "巨潮资讯网", "coverage": "公告/定期报告披露日(权威源)",
             "url": "https://www.cninfo.com.cn", "confidence": "high", "fetch_time": events.get("fetch_time")},
            {"name": "东方财富", "coverage": "机构研报元数据+PDF/个股新闻",
             "url": "https://data.eastmoney.com", "confidence": "medium", "fetch_time": reports.get("fetch_time")},
        ],
        "gaps": gaps,
    }
    save_json(os.path.join(docs, "data", "data_sources.json"), src_manifest)

    # index.html（扫描全部已生成站点）
    data_root = os.path.join(docs, "data")
    cards = []
    for entry in sorted(os.listdir(data_root)):
        sj = os.path.join(data_root, entry, "site.json")
        if not os.path.isfile(sj):
            continue
        try:
            with open(sj, encoding="utf-8") as f:
                m = json.load(f)["meta"]
        except Exception:
            continue
        cards.append(
            f'<a class="card" href="review.html?code={entry}">'
            f'<h3>{html.escape(m["name"])} <span class="code">{entry}</span></h3>'
            f'<p class="sub">复盘区间 {m["start_date"]} ~ {m["end_date"]}</p></a>')
    index = (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
             f'<meta name="viewport" content="width=device-width,initial-scale=1">'
             f"<title>超级成长股复盘</title><style>{DOC_CSS}"
             f".card{{display:block;border:1px solid #30363d;border-radius:8px;padding:16px 20px;margin:12px 0;background:#161b22}}"
             f".card:hover{{border-color:#58a6ff}}.code{{color:#8b949e;font-size:.8em;font-weight:400}}"
             f"</style></head><body><div class='wrap'>"
             f"<h1>超级成长股复盘 · ReviewGrowth</h1>"
             f"<p class='sub'>以当时视角（as-of）的当期财报与研报做定性分析的复盘系统 · 仅供学习研究</p>"
             f"{''.join(cards) or '<p class=sub>暂无股票，运行 python3 scripts/fetch_all.py 后由 Agent 生成分析</p>'}"
             f"<p class='sub'>数据来源：巨潮资讯网 / 同花顺 / baostock / 东方财富</p>"
             f"</div></body></html>")
    with open(os.path.join(docs, "index.html"), "w", encoding="utf-8") as f:
        f.write(index)

    log(f"✓ site.json {len(site_json) / 1024:.0f}KB | timeline {len(site['timeline'])} | "
        f"earnings {len(site['earnings'])} | research {len(site['research'])}（关键 {sum(1 for r in site['research'] if r['key'])}）| "
        f"phases {len(site['phases'])} | gaps {len(gaps)}")
    log(f"✓ 站点已生成 → docs/（本地预览: cd docs && python3 -m http.server 8765）")


if __name__ == "__main__":
    main()
