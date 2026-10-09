"""抓取财报：三表（累计口径实证判定→单季差分）+ 财务指标 + 巨潮公告日核验

输出: data/{code}/earnings.json
说明:
- 三表区间从 2017-01-01 起（2018Q1 单季需要 2017Q4 累计底座；2019Q1 YoY 需要 2018Q1）。
- 披露日 report_date_ms 直接作为 available_at；与巨潮定期报告公告日 ±3 天内视为高置信。
- 扣非净利/存货/合同负债 不在接口字段中（已知缺口，由分析层从财报 PDF 补充或记 gap）。
"""

import os
import sys
from datetime import date

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import (cfg_argparse, d2ms, data_dir, is_fresh, load_config,  # noqa: E402
                    load_json, log, ms_to_date, now_str, save_json)
from fuyao_api import fetch_statement, fetch_indicators  # noqa: E402

INCOME_FLOW = ["operating_income", "operating_costs", "operating_expenses", "sales_fee",
               "manage_fee", "research_and_development_expenses", "operating_profit",
               "profit_total", "income_tax_expense", "net_profit",
               "parent_holder_net_profit", "basic_eps"]
CASH_FLOW = ["act_cash_flow_net", "invest_cash_flow_net", "financing_cash_flow_net",
             "pay_fixed_assets_etc_cash", "cash_equivalents_net_addition"]
BALANCE_POINT = ["assets_total", "total_current_assets", "cash", "accounts_receivable",
                 "total_debt", "holder_equity_total"]
REPORT_TYPE = {"Q1": "一季报", "Q2": "中报", "Q3": "三季报", "Q4": "年报"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def diff(a, b):
    if a is None or b is None:
        return None
    return a - b


def ratio(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def yoy(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def index_rows(rows):
    """[(fiscal_year, fiscal_period)] → row"""
    out = {}
    for r in rows:
        y, q = r.get("fiscal_year"), r.get("fiscal_period")
        if y and q:
            out[(int(y), q)] = r
    return out


def _span_chunks(start, end, years=8):
    """fuyao 三表要求区间跨度 <= 10 年 → 按 8 年分块"""
    from datetime import date, timedelta
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    while s <= e:
        ce = min(date(s.year + years, s.month, s.day), e)
        yield s.isoformat(), ce.isoformat()
        s = ce + timedelta(days=1)


def fetch_statement_chunked(thscode, kind, start, end):
    rows = []
    for cs, ce in _span_chunks(start, end):
        rows.extend(fetch_statement(thscode, kind, d2ms(cs), d2ms(ce, end_of_day=True)))
    return rows


def prev_period(y, q):
    qn = int(q[1])
    if qn == 1:
        return (y - 1, "Q4")
    return (y, f"Q{qn - 1}")


def main():
    ap = cfg_argparse("抓取财报三表与指标")
    args = ap.parse_args()
    cfg = load_config(args.code)
    code = cfg["code"]
    out_path = os.path.join(data_dir(code), "earnings.json")
    if not args.force and is_fresh(out_path, cfg["refresh_days"]):
        log("earnings.json 新鲜，跳过")
        return

    thscode = cfg["thscode"]
    sy, ey = int(cfg["start_date"][:4]), int(cfg["end_date"][:4])
    st_start, st_end = f"{sy - 2}-01-01", f"{ey + 1}-06-30"
    log(f"抓取三表 {st_start} ~ {st_end}（区间模式，按 8 年分块）")
    income = index_rows(fetch_statement_chunked(thscode, "income", st_start, st_end))
    balance = index_rows(fetch_statement_chunked(thscode, "balance", st_start, st_end))
    cashflow = index_rows(fetch_statement_chunked(thscode, "cashflow", st_start, st_end))
    if not income:
        raise RuntimeError("利润表为空，终止")
    log(f"三表: 利润{len(income)}期 / 负债{len(balance)}期 / 现金流{len(cashflow)}期")

    # 累计口径实证判定（同年 Q1<=Q2<=Q3<=Q4 单调）
    ok_years, years = 0, 0
    for y in range(sy, ey + 1):
        rows = [income.get((y, f"Q{i}")) for i in range(1, 5)]
        vals = [num(r.get("operating_income")) if r else None for r in rows]
        if all(v is not None for v in vals):
            years += 1
            if vals[0] <= vals[1] <= vals[2] <= vals[3]:
                ok_years += 1
    cum_mode = "cumulative" if years and ok_years / years >= 0.75 else "single"
    log(f"口径判定: {cum_mode}（{ok_years}/{years} 年单调）")

    # 单季值
    single_income, single_cash = {}, {}
    for (y, q), r in income.items():
        cur = {f: num(r.get(f)) for f in INCOME_FLOW}
        if cum_mode == "single":
            single_income[(y, q)] = cur
        else:
            prev_row = income.get(prev_period(y, q))
            single_income[(y, q)] = {f: (cur[f] if q == "Q1" or prev_row is None
                                         else diff(cur[f], num(prev_row.get(f))))
                                     for f in INCOME_FLOW}
    for (y, q), r in cashflow.items():
        cur = {f: num(r.get(f)) for f in CASH_FLOW}
        if cum_mode == "single":
            single_cash[(y, q)] = cur
        else:
            prev_row = cashflow.get(prev_period(y, q))
            single_cash[(y, q)] = {f: (cur[f] if q == "Q1" or prev_row is None
                                       else diff(cur[f], num(prev_row.get(f))))
                                   for f in CASH_FLOW}

    # 指标（16 期）
    ind_map = {}
    for y in range(sy, ey + 1):
        for qi in range(1, 5):
            ind = fetch_indicators(thscode, f"{y}-{qi}")
            if ind:
                ind_map[(y, f"Q{qi}")] = ind
    log(f"指标: {len(ind_map)} 期")

    # 巨潮公告核验
    ev = load_json(os.path.join(data_dir(code), "events.json")) or {}
    ann_by_period = {}
    for a in ev.get("announcements", []):
        p = a.get("report_period")
        if p:
            ann_by_period.setdefault(p, []).append(a)

    def ttm(num_map, y, q, field):
        """TTM = 本年累计 + 上年全年 - 上年同期累计"""
        cur = num_map.get((y, q))
        if cur is None:
            return None
        if q == "Q4":
            return cur.get(field)
        prev_fy, prev_same = num_map.get((y - 1, "Q4")), num_map.get((y - 1, q))
        if prev_fy is None or prev_same is None:
            return None
        a, b, c = cur.get(field), prev_fy.get(field), prev_same.get(field)
        if a is None or b is None or c is None:
            return None
        return a + b - c

    income_num = {k: {f: num(v.get(f)) for f in ("operating_income", "parent_holder_net_profit")}
                  for k, v in income.items()}

    periods, gap_list = [], []
    for y in range(sy, ey + 1):
        for qi in range(1, 5):
            q = f"Q{qi}"
            ir = income.get((y, q))
            if not ir:
                continue
            period = f"{y}{q}"
            period_end = ms_to_date(ir.get("period_end_ms")) if ir.get("period_end_ms") else None
            fuyao_rdate = ms_to_date(ir.get("report_date_ms")) if ir.get("report_date_ms") else None
            cum = {f: num(ir.get(f)) for f in INCOME_FLOW}
            bal = {f: num((balance.get((y, q)) or {}).get(f)) for f in BALANCE_POINT}
            cs = {f: num((cashflow.get((y, q)) or {}).get(f)) for f in CASH_FLOW}
            sq = single_income.get((y, q), {})
            sqc = single_cash.get((y, q), {})
            sq_prev = single_income.get(prev_period(y, q), {})
            sq_prev_year = single_income.get((y - 1, q), {})

            # 披露日以巨潮定期报告公告为唯一权威源（fuyao report_date_ms 实测存在约一年偏移，仅作审计留存）
            anns = ann_by_period.get(period, [])
            full = [a for a in anns if "全文" in a["title"]]
            pick = sorted(full or anns, key=lambda a: a["date"])
            ann = pick[0] if pick else None
            gaps_here = []
            if ann:
                report_date = ann["date"]
                confidence, note = "high", "披露日=巨潮定期报告公告日"
                if period_end:
                    d1, d2 = date.fromisoformat(period_end), date.fromisoformat(report_date)
                    lag = (d2 - d1).days
                    if not (10 <= lag <= 140):
                        confidence = "medium"
                        note += f"；距报告期结束{lag}天（异常，待核）"
                        gaps_here.append(f"{period} 公告日 {report_date} 距报告期末 {lag} 天，超出常规披露窗口")
            else:
                report_date = fuyao_rdate
                confidence, note = "low", "未匹配到巨潮定期报告公告；fuyao 接口披露日疑似系统性偏移约一年，待验证"
                gaps_here.append(f"{period} 缺少巨潮公告核对（接口披露日 {fuyao_rdate} 不可信）")
            gap_list.extend(gaps_here)

            gross_cum = 1 - ratio(cum["operating_costs"], cum["operating_income"]) if cum["operating_costs"] is not None else None
            gross_sq = 1 - ratio(sq.get("operating_costs"), sq.get("operating_income")) if sq.get("operating_costs") is not None else None
            exp_cum = sum(x for x in [cum["sales_fee"], cum["manage_fee"],
                                      cum["research_and_development_expenses"]] if x is not None) or None
            exp_sq = sum(x for x in [sq.get("sales_fee"), sq.get("manage_fee"),
                                     sq.get("research_and_development_expenses")] if x is not None) or None
            ttm_rev = ttm(income_num, y, q, "operating_income")
            ttm_parent = ttm(income_num, y, q, "parent_holder_net_profit")

            periods.append({
                "period": period,
                "fiscal_year": y, "fiscal_period": q,
                "report_type": REPORT_TYPE[q],
                "report_date": report_date,
                "available_at": report_date,
                "period_end": period_end,
                "fuyao_report_date": fuyao_rdate,
                "confidence": confidence,
                "confidence_note": note,
                "announcement": ({"date": ann["date"], "title": ann["title"], "url": ann["url"]}
                                 if ann else None),
                "cumulative": cum,
                "single_q": sq,
                "balance": bal,
                "cashflow_cum": cs,
                "cashflow_single": sqc,
                "yoy": {
                    "revenue_yoy": yoy(sq.get("operating_income"), sq_prev_year.get("operating_income")),
                    "parent_net_yoy": yoy(sq.get("parent_holder_net_profit"),
                                          sq_prev_year.get("parent_holder_net_profit")),
                    "net_profit_yoy": yoy(sq.get("net_profit"), sq_prev_year.get("net_profit")),
                },
                "qoq": {
                    "revenue_qoq": yoy(sq.get("operating_income"), sq_prev.get("operating_income")),
                    "parent_net_qoq": yoy(sq.get("parent_holder_net_profit"),
                                          sq_prev.get("parent_holder_net_profit")),
                },
                "margins": {
                    "cum": {"gross": gross_cum,
                            "net": ratio(cum["net_profit"], cum["operating_income"]),
                            "parent_net": ratio(cum["parent_holder_net_profit"], cum["operating_income"])},
                    "single": {"gross": gross_sq,
                               "net": ratio(sq.get("net_profit"), sq.get("operating_income")),
                               "parent_net": ratio(sq.get("parent_holder_net_profit"),
                                                   sq.get("operating_income"))},
                },
                "expense_ratios": {
                    "cum_total": ratio(exp_cum, cum["operating_income"]),
                    "single_total": ratio(exp_sq, sq.get("operating_income")),
                    "single_rd": ratio(sq.get("research_and_development_expenses"),
                                       sq.get("operating_income")),
                    "single_sales": ratio(sq.get("sales_fee"), sq.get("operating_income")),
                    "single_manage": ratio(sq.get("manage_fee"), sq.get("operating_income")),
                },
                "cash": {
                    "ocf_single": sqc.get("act_cash_flow_net"),
                    "ocf_cum": cs["act_cash_flow_net"],
                    "cash_content_single": ratio(sqc.get("act_cash_flow_net"),
                                                 sq.get("parent_holder_net_profit")),
                    "cash_content_cum": ratio(cs["act_cash_flow_net"],
                                              cum["parent_holder_net_profit"]),
                    "capex_single": sqc.get("pay_fixed_assets_etc_cash"),
                },
                "ttm": {"revenue": ttm_rev, "parent_net": ttm_parent,
                        "ar_to_revenue": ratio(bal["accounts_receivable"], ttm_rev)},
                "indicators": ind_map.get((y, q), {}),
            })

    save_json(out_path, {
        "fetch_time": now_str(),
        "source": "fuyao 三表+指标（数值）；披露日=巨潮定期报告公告（fuyao report_date_ms 实测偏移约一年，仅审计留存）",
        "cumulative_mode": cum_mode,
        "known_gaps": ["扣非净利/存货/合同负债 不在 fuyao 三表字段中，需从财报PDF补充或记为缺口"],
        "gaps": gap_list,
        "periods": periods,
        "raw": {
            "income": [dict(r, fiscal_year=int(r["fiscal_year"])) for r in income.values()],
            "balance": [dict(r, fiscal_year=int(r["fiscal_year"])) for r in balance.values()],
            "cashflow": [dict(r, fiscal_year=int(r["fiscal_year"])) for r in cashflow.values()],
        },
    })
    log(f"earnings.json: {len(periods)} 期（{periods[0]['period']} ~ {periods[-1]['period']}，口径={cum_mode}）")
    for p in periods:
        flag = "" if p["confidence"] == "high" else f"  ⚠{p['confidence_note']}"
        log(f"  {p['period']} 披露 {p['report_date']} 单季营收 {p['single_q'].get('operating_income', 0)/1e8:.1f}亿"
            f" 归母 {p['single_q'].get('parent_holder_net_profit', 0)/1e8:.1f}亿{flag}")


if __name__ == "__main__":
    main()
