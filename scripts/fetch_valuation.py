"""抓取估值历史（baostock 日频 PE/PB/PS）+ as-of PE 历史分位 + 财报日 PEG

输出: data/{code}/valuation.json
依赖: 先运行 fetch_earnings（PEG 需要财报与指标）；缺失时 PEG 部分跳过并记 gap。
"""

import bisect
import os
import sys
from datetime import date, timedelta

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import (cfg_argparse, data_dir, is_fresh, load_config, load_json,  # noqa: E402
                    log, now_str, save_json)
from baostock_api import valuation_history, logout  # noqa: E402
from tencent_api import fetch_kline_tx, tx_symbol  # noqa: E402

MIN_OBS = 120   # 分位计算最少样本（约半年）


def pe_approx_series(cfg, code, start, end):
    """baostock 不可用时的 PE-TTM 近似：不复权收盘价 / EPS-TTM（按披露日 as-of 生效）。
    EPS-TTM = 本期累计EPS + 上年年报EPS - 上年同期累计EPS（Q4 即年报值）。
    局限：未计报告期之间的股本变动基数差异；PB/PS 缺失。返回升序 [{date,pe,pb,ps,turnover}] 或 None。"""
    bars = fetch_kline_tx(tx_symbol(cfg["thscode"]), start, end, adjust=None)
    earn = load_json(os.path.join(data_dir(code), "earnings.json")) or {}
    raw = earn.get("raw", {}).get("income") or []
    if not bars or not raw:
        return None
    eps_map = {}
    for r in raw:
        try:
            eps_map[(int(r["fiscal_year"]), r["fiscal_period"])] = float(r.get("basic_eps"))
        except (TypeError, ValueError, KeyError):
            continue
    pts = []
    for p in earn.get("periods", []):
        if not p.get("available_at"):
            continue
        y, q = p["fiscal_year"], p["fiscal_period"]
        cur = eps_map.get((y, q))
        if q == "Q4":
            ttm = cur
        else:
            ttm = None
            a, b, c = cur, eps_map.get((y - 1, "Q4")), eps_map.get((y - 1, q))
            if a is not None and b is not None and c is not None:
                ttm = a + b - c
        if ttm and ttm > 0:
            pts.append((p["available_at"], ttm))
    if not pts:
        return None
    pts.sort()
    out = []
    for b in bars:
        ttm = None
        for d, e in pts:
            if d <= b["d"]:
                ttm = e
            else:
                break
        out.append({"date": b["d"], "pe": (b["c"] / ttm) if ttm else None,
                    "pb": None, "ps": None, "turnover": None})
    return out


def main():
    ap = cfg_argparse("抓取估值历史与分位")
    args = ap.parse_args()
    cfg = load_config(args.code)
    code = cfg["code"]
    out_path = os.path.join(data_dir(code), "valuation.json")
    if not args.force and is_fresh(out_path, cfg["refresh_days"]):
        log("valuation.json 新鲜，跳过")
        return

    start = cfg.get("warmup_start") or cfg["start_date"]
    end = (date.fromisoformat(cfg["end_date"])
           + timedelta(days=cfg.get("extend_after_days", 120))).isoformat()
    log(f"baostock 估值历史 {start} ~ {end}")
    series = valuation_history(code, start, end)
    vsource = "baostock peTTM/pbMRQ/psTTM（分位=as-of 扩展窗口，min_obs=%d）" % MIN_OBS
    gaps = []
    if not series:
        log("  ↳ baostock 不可用，尝试 PE 近似（不复权价/EPS-TTM）")
        series = pe_approx_series(cfg, code, start, end)
        if series:
            vsource = "PE 近似：不复权收盘价 / EPS-TTM（baostock 降级后备源）"
            gaps.append({"scope": "估值明细", "reason": "baostock 数据服务不可达",
                         "impact": "中：PB/PS 缺失；PE 为近似值（EPS 基数与股本变动未精细还原）",
                         "mitigation": "服务恢复后 --only fetch_valuation --force 重抓"})
            log(f"  ↳ PE 近似序列 {len(series)} 日")
    if not series:
        gaps.append({"scope": "估值历史", "reason": "baostock 与腾讯均不可用",
                     "impact": "PE/PB/PS 历史与分位缺失，页面显示缺口条"})
        save_json(out_path, {"fetch_time": now_str(), "source": vsource,
                             "status": "unavailable", "series": [], "peg_at_earnings": [], "gaps": gaps})
        log("⚠ valuation.json: 估值不可用（降级）")
        return

    # as-of 分位：对每个交易日，取该日及之前样本（>=MIN_OBS）计算 PE 分位
    sorted_pe, out = [], []
    for it in series:
        pe = it["pe"]
        if pe is not None and pe > 0:
            bisect.insort(sorted_pe, pe)
        pctile = None
        if pe is not None and len(sorted_pe) >= MIN_OBS:
            pctile = round(bisect.bisect_left(sorted_pe, pe) / len(sorted_pe) * 100, 1)
        out.append({**it, "pe_pctile": pctile})

    # 财报日 PEG（增长用同花顺累计归母净利同比增速指标）
    earn = load_json(os.path.join(data_dir(code), "earnings.json")) or {}
    peg_rows = []
    if earn.get("periods"):
        def pe_at(d):
            """披露日首个交易日收盘 PE"""
            for r in out:
                if r["date"] >= d:
                    return r
            return None
        for p in earn["periods"]:
            if not p.get("report_date"):
                continue
            row = pe_at(p["report_date"])
            g = (p.get("indicators") or {}).get("calculate_parent_holder_net_profit_yoy_growth_ratio")
            peg = None
            if row and row["pe"] and g and g > 0:
                peg = round(row["pe"] / g, 2)
            peg_rows.append({
                "period": p["period"], "date": row["date"] if row else None,
                "pe": row["pe"] if row else None,
                "growth_yoy_pct": g, "peg": peg,
            })
    else:
        gaps.append({"scope": "PEG", "reason": "earnings.json 缺失，先运行 fetch_earnings",
                     "impact": "财报日 PEG 缺失"})

    logout()
    save_json(out_path, {
        "fetch_time": now_str(),
        "source": vsource,
        "status": "ok",
        "series": out,
        "peg_at_earnings": peg_rows,
        "gaps": gaps,
    })
    last = out[-1]
    log(f"valuation.json: {len(out)} 日（最新 {last['date']} PE={last['pe']} 分位={last['pe_pctile']}）"
        f" PEG覆盖 {len([r for r in peg_rows if r['peg']])} 期")


if __name__ == "__main__":
    main()
