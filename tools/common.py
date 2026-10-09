"""共享工具：路径 / 日志 / JSON 读写 / 中国时区日期"""

import json
import os
from datetime import datetime, timezone, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CN_TZ = timezone(timedelta(hours=8))


def now_cn():
    return datetime.now(CN_TZ)


def now_str():
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_cn().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config(code):
    path = os.path.join(REPO, "config", "stocks", f"{code}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def data_dir(code):
    path = os.path.join(REPO, "data", code)
    os.makedirs(path, exist_ok=True)
    return path


def analysis_dir(code):
    path = os.path.join(REPO, "analysis", code)
    os.makedirs(path, exist_ok=True)
    return path


def docs_data_dir(code):
    path = os.path.join(REPO, "docs", "data", code)
    os.makedirs(path, exist_ok=True)
    return path


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def is_fresh(path, refresh_days):
    """文件存在且 fetch_time 距今天数 < refresh_days 时视为新鲜（增量跳过）。"""
    if not os.path.exists(path):
        return False
    d = load_json(path)
    if not d or not d.get("fetch_time"):
        return False
    try:
        t = datetime.strptime(d["fetch_time"][:10], "%Y-%m-%d")
    except ValueError:
        return False
    return (now_cn().replace(tzinfo=None) - t).days < refresh_days


def cfg_argparse(desc):
    """fetch 脚本共享的参数：--code / --force"""
    import argparse
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--code", required=True, help="股票代码，如 300750")
    ap.add_argument("--force", action="store_true", help="忽略缓存强制刷新")
    return ap


def d2ms(date_str, end_of_day=False):
    """'YYYY-MM-DD' → 毫秒时间戳（+08:00 语义）"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CN_TZ)
    if end_of_day:
        dt += timedelta(hours=23, minutes=59, seconds=59)
    return int(dt.timestamp() * 1000)


def ms_to_date(ms):
    """date_ms → YYYY-MM-DD。兼容 UTC 零点 / 本地零点两种语义：
    本地转换后若时刻 >= 16 点则视为 UTC 零点，日期 +1（同 zhuang fetch.py）。"""
    dt = datetime.fromtimestamp(ms / 1000.0)
    d = dt.date()
    if dt.hour >= 16:
        d = d + timedelta(days=1)
    return d.strftime("%Y-%m-%d")
