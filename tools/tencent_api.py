"""腾讯行情 K 线（后备源，baostock 不可用时使用）

端点: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,{start},{end},{count},{qfq}
- 个股: adjust="qfq" → 前复权；adjust=None → 不复权（键名对应 qfqday / day）
- 指数: 无复权语义，键名固定 day
- 行格式: [date, open, close, high, low, volume(手), ...]（注意 open,close,high,low 顺序）
- 单次返回上限约 2000 根 → 按 ≤600 自然日分块请求
"""

import time

import requests

_TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/"}


def _chunks(start_date, end_date, days=600):
    from datetime import date, timedelta
    cur = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur.isoformat(), nxt.isoformat()
        cur = nxt + timedelta(days=1)


def fetch_kline_tx(symbol, start_date, end_date, adjust="qfq"):
    """symbol 形如 'sz300308' / 'sz399006'。返回升序 [{d,o,h,l,c,v,amount}] 或 None。
    v 统一转为股（腾讯原始单位为手）；amount 腾讯该端点不提供，置 0。"""
    key = "qfqday" if adjust == "qfq" else "day"
    out = {}
    for cs, ce in _chunks(start_date, end_date):
        for attempt in range(2):
            try:
                # 注意：第 6 位参数（复权类型，可为空）必须存在（含尾逗号），缺失会返回空列表
                r = requests.get(_TX_URL,
                                 params={"param": f"{symbol},day,{cs},{ce},800,{adjust or ''}"},
                                 headers=_HEADERS, timeout=20)
                d = r.json()
                data = (d.get("data") or {}).get(symbol) or {}
                rows = data.get(key) or data.get("day") or []
                for row in rows:
                    try:
                        out[row[0]] = {
                            "d": row[0],
                            "o": float(row[1]), "c": float(row[2]),
                            "h": float(row[3]), "l": float(row[4]),
                            "v": float(row[5]) * 100 if len(row) > 5 and row[5] else 0.0,
                            "amount": 0.0,
                        }
                    except (TypeError, ValueError, IndexError):
                        continue
                break
            except Exception:
                if attempt == 0:
                    time.sleep(1.5)
    if not out:
        return None
    return [out[k] for k in sorted(out)]


def tx_symbol(thscode):
    """'300308.SZ' → 'sz300308'；'399006.SZ' → 'sz399006'；'000300.SH' → 'sh000300'"""
    code, _, mkt = thscode.partition(".")
    return ("sh" if mkt.upper() == "SH" else "sz") + code
