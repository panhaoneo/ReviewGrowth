"""同花顺 fuyao REST 客户端（K线 / 三表 / 指标 / 指数目录）

鉴权：X-api-key。密钥加载顺序：
  1) <repo>/data/THS_API_KEY
  2) 环境变量 FUYAO_API_KEY / HITHINK_FINANCE_API_KEY
  3) 本机 zhuang 项目回退路径（仅本机便捷）
响应信封：{code, message, data}，code==0 成功；data.item 为列表。
"""

import os
import time

import requests

from common import REPO, log

BASE = "https://fuyao.aicubes.cn"

_KEY_FALLBACKS = [
    os.path.join(REPO, "data", "THS_API_KEY"),
    "/Users/panhao01/Code/zhuang/data/THS_API_KEY",
]


def _load_api_key():
    for path in _KEY_FALLBACKS:
        if os.path.exists(path):
            with open(path) as f:
                key = f.read().strip()
            if key:
                return key
    for env in ("FUYAO_API_KEY", "HITHINK_FINANCE_API_KEY"):
        key = os.environ.get(env)
        if key:
            return key.strip()
    return ""


API_KEY = _load_api_key()
HEADERS = {"X-api-key": API_KEY}

_RETRY_CODES = {4001, 429}


def api_get(endpoint, params=None, timeout=30, retries=2):
    """返回完整响应 dict（code==0 或最终失败态），网络异常重试后返回 None。"""
    for i in range(retries + 1):
        try:
            r = requests.get(f"{BASE}{endpoint}", params=params, headers=HEADERS, timeout=timeout)
            d = r.json()
            if d.get("code") == 0:
                return d
            if d.get("code") in _RETRY_CODES and i < retries:
                time.sleep(1.5 * (i + 1))
                continue
            log(f"  ⚠ fuyao {endpoint} code={d.get('code')} msg={d.get('message')}")
            return d
        except Exception as e:
            if i < retries:
                time.sleep(1.0 * (i + 1))
                continue
            log(f"  ⚠ fuyao {endpoint} 请求异常: {e}")
    return None


def safe_items(data):
    if data and isinstance(data, dict) and data.get("code") == 0:
        d = data.get("data")
        if isinstance(d, dict):
            return d.get("item") or []
    return []


# ── 行情 ──

def fetch_bars(thscode, start_ms, end_ms):
    """个股日K（前复权）。返回 [{d,o,h,l,c,v,amount}] 升序。"""
    data = api_get("/api/a-share/prices/historical",
                   {"thscode": thscode, "interval": "1d",
                    "start": start_ms, "end": end_ms, "adjust": "forward"}, timeout=20)
    out = []
    for b in safe_items(data):
        ms = b.get("date_ms") or 0
        if not ms:
            continue
        out.append({
            "date_ms": ms,
            "open": float(b.get("open_price") or 0),
            "high": float(b.get("high_price") or 0),
            "low": float(b.get("low_price") or 0),
            "close": float(b.get("close_price") or 0),
            "vol": float(b.get("volume") or 0),
            "amount": float(b.get("turnover") or 0),
        })
    out.sort(key=lambda x: x["date_ms"])
    return out


def fetch_index_bars(thscode, start_ms, end_ms):
    """指数日K（宽基 / .TI 同花顺指数均走此端点）。返回 [{date_ms, open, high, low, close, vol, amount}]。"""
    data = api_get("/api/a-share-index/prices/historical",
                   {"thscode": thscode, "interval": "1d",
                    "start": start_ms, "end": end_ms}, timeout=20)
    out = []
    for b in safe_items(data):
        ms = b.get("date_ms") or 0
        if not ms:
            continue
        out.append({
            "date_ms": ms,
            "open": float(b.get("open_price") or 0),
            "high": float(b.get("high_price") or 0),
            "low": float(b.get("low_price") or 0),
            "close": float(b.get("close_price") or 0),
            "vol": float(b.get("volume") or 0),
            "amount": float(b.get("turnover") or 0),
        })
    out.sort(key=lambda x: x["date_ms"])
    return out


# ── 财务 ──

_STATEMENT_PATHS = {
    "income": "/api/a-share/financials/income-statements",
    "balance": "/api/a-share/financials/balance-sheets",
    "cashflow": "/api/a-share/financials/cash-flow-statements",
}


def fetch_statement(thscode, kind, start_ms, end_ms):
    """三表区间查询（period=quarterly）。返回原始 item 列表。"""
    data = api_get(_STATEMENT_PATHS[kind],
                   {"thscode": thscode, "period": "quarterly",
                    "start": start_ms, "end": end_ms}, timeout=25)
    return safe_items(data)


def fetch_indicators(thscode, report, timeout=15):
    """单期财务指标。report 格式 'YYYY-N'（N=1..4）。返回扁平 {index_id: float}。"""
    data = api_get("/api/a-share/financials/indicators",
                   {"thscode": thscode, "report": report}, timeout=timeout)
    out = {}
    if data and data.get("code") == 0:
        for ab in (data.get("data") or {}).get("abilities", []):
            for ind in ab.get("indicators", []):
                v = ind.get("value")
                if v is None:
                    continue
                try:
                    out[ind["index_id"]] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


# ── 指数目录 ──

def fetch_index_catalog(tag="cn_concept"):
    """同花顺指数目录。tag: cn_concept / industry / region / tszs。返回 [{thscode,name,...}]。"""
    data = api_get("/api/a-share-index/catalog/ths-index-list", {"tag": tag}, timeout=30)
    return safe_items(data)
