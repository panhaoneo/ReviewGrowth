"""抓取巨潮公告（含调研纪要/业绩预告识别）+ 东财个股新闻

输出: data/{code}/events.json
说明: 东财新闻仅覆盖近期，2019-2021 为已知缺口（写入 coverage_note，由分析层记入 gaps）。
"""

import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import (cfg_argparse, data_dir, is_fresh, load_config, log,  # noqa: E402
                    now_str, save_json)
from cninfo_api import query_announcements  # noqa: E402
from em_api import eastmoney_stock_news  # noqa: E402

_REPORT_RE = re.compile(
    r"(\d{4})\s*年\s*(年度报告|半年度报告|一季度报告|第一季度报告|三季度报告|第三季度报告|中期报告)")
_PERIOD_MAP = {"年度报告": "Q4", "半年度报告": "Q2", "中期报告": "Q2",
               "一季度报告": "Q1", "第一季度报告": "Q1",
               "三季度报告": "Q3", "第三季度报告": "Q3"}


def classify(title, ann_type):
    is_irm = "投资者关系活动记录表" in title
    is_forecast = ("业绩预告" in title) or ("业绩快报" in title) or ("业绩预告" in (ann_type or ""))
    period = None
    m = _REPORT_RE.search(title)
    bad = ("摘要", "英文", "更正", "提示", "预约", "推迟", "变更", "取消", "补充")
    if m and not any(b in title for b in bad):
        period = f"{m.group(1)}{_PERIOD_MAP[m.group(2)]}"
    return is_irm, is_forecast, period


def main():
    ap = cfg_argparse("抓取公告与新闻")
    args = ap.parse_args()
    cfg = load_config(args.code)
    code = cfg["code"]
    out_path = os.path.join(data_dir(code), "events.json")
    if not args.force and is_fresh(out_path, cfg["refresh_days"]):
        log("events.json 新鲜，跳过")
        return

    from datetime import date, timedelta
    end_plus = (date.fromisoformat(cfg["end_date"]) + timedelta(days=120)).isoformat()
    se = f"{cfg['start_date']}~{end_plus}"
    log(f"抓取巨潮公告 {se}（龙头股公告量大，按 30/页 分页）")
    anns = query_announcements(code, se, page_size=30, max_pages=100)
    log(f"公告 {len(anns)} 条")
    out = []
    n_irm = n_fc = n_rep = 0
    for a in anns:
        is_irm, is_forecast, period = classify(a["title"], a["type"])
        n_irm += is_irm
        n_fc += is_forecast
        n_rep += period is not None
        out.append({**a, "is_irm": is_irm, "is_forecast": is_forecast, "report_period": period})

    log("抓取东财个股新闻（代码 + 公司名双路）")
    news, seen = [], set()
    for kw in (code, cfg["name"]):
        for n in eastmoney_stock_news(kw, page_size=50):
            key = n["url"] or n["title"]
            if key in seen:
                continue
            seen.add(key)
            news.append(n)
    log(f"新闻 {len(news)} 条")

    save_json(out_path, {
        "fetch_time": now_str(),
        "source": "巨潮资讯网(公告) + 东方财富(新闻)",
        "announcements": out,
        "news": {
            "coverage_note": "东财新闻接口仅覆盖近期，2019-2021 历史新闻缺失为已知缺口",
            "items": news,
        },
        "stats": {"announcements": len(out), "irm": n_irm,
                  "forecast": n_fc, "report_announcements": n_rep, "news": len(news)},
    })
    log(f"events.json: 公告{len(out)}（调研{n_irm}/预告{n_fc}/定期报告{n_rep}）新闻{len(news)}")


if __name__ == "__main__":
    main()
