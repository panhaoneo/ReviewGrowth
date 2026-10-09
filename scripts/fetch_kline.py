"""抓取个股前复权日K + 基准指数日K（基准走 baostock，fuyao 指数接口无历史）

输出: data/{code}/kline.json, data/{code}/benchmarks.json
"""

import os
import sys
from datetime import date, timedelta

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import (cfg_argparse, data_dir, is_fresh, load_config,  # noqa: E402
                    log, now_str, save_json)
from baostock_api import index_kline, stock_kline, logout  # noqa: E402


def ext_end(cfg):
    """行情数据尾部延伸，覆盖区间外年报披露与 T+60 反馈窗口"""
    return (date.fromisoformat(cfg["end_date"])
            + timedelta(days=cfg.get("extend_after_days", 120))).isoformat()


def fetch_stock_kline(cfg, force):
    code = cfg["code"]
    out_path = os.path.join(data_dir(code), "kline.json")
    if not force and is_fresh(out_path, cfg["refresh_days"]):
        log(f"kline.json 新鲜（{cfg['refresh_days']}天内），跳过")
        return
    start, end = cfg.get("warmup_start") or cfg["start_date"], ext_end(cfg)
    log(f"抓取 {cfg['thscode']} 日K {start} ~ {end}（baostock 前复权）")
    bars = stock_kline(code, start, end, adjustflag="2")
    if not bars:
        raise RuntimeError("K线为空，终止")
    save_json(out_path, {
        "fetch_time": now_str(),
        "source": "baostock 前复权（事件式复权；2023-04-26 10转8 致此前价格约0.51倍缩放）",
        "symbol": cfg["thscode"],
        "bars": bars,
    })
    log(f"kline.json: {len(bars)} 根（{bars[0]['d']} ~ {bars[-1]['d']}）")


def fetch_benchmarks(cfg, force):
    code = cfg["code"]
    out_path = os.path.join(data_dir(code), "benchmarks.json")
    if not force and is_fresh(out_path, cfg["refresh_days"]):
        log("benchmarks.json 新鲜，跳过")
        return
    start = cfg.get("warmup_start") or cfg["start_date"]
    end = ext_end(cfg)
    benchmarks, gaps = {}, []
    for tc, meta in cfg["benchmarks"].items():
        log(f"抓取基准指数 {tc} {meta['name']}（baostock）")
        bars = index_kline(meta["bs"], start, end)
        if bars:
            benchmarks[tc] = {"name": meta["name"], "role": meta.get("role", "broad"), "bars": bars}
        else:
            gaps.append({"scope": f"benchmark {tc} {meta['name']}", "reason": "baostock 返回为空",
                         "impact": "相对收益计算缺失该基准"})
    logout()
    if not benchmarks:
        raise RuntimeError("全部基准指数抓取失败，终止")
    save_json(out_path, {
        "fetch_time": now_str(),
        "source": "baostock 指数日K",
        "benchmarks": benchmarks,
        "gaps": gaps,
    })
    summary = ", ".join(f"{v['name']}({len(v['bars'])}根)" for v in benchmarks.values())
    log(f"benchmarks.json: {summary}")


def main():
    ap = cfg_argparse("抓取日K与基准指数")
    args = ap.parse_args()
    cfg = load_config(args.code)
    fetch_stock_kline(cfg, args.force)
    fetch_benchmarks(cfg, args.force)


if __name__ == "__main__":
    main()
