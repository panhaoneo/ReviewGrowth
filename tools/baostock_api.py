"""baostock 客户端：日频估值历史（peTTM/pbMRQ/psTTM）+ 指数/个股日K。

说明：fuyao 指数接口无 2019-2022 历史（实测仅近两年），因此基准指数与估值历史均走 baostock。
不依赖 pandas：原生 rs.next()/get_row_data() 迭代。登录/查询失败返回 None（调用方降级）。
"""

import io
import contextlib

from common import log

_LOGGED_IN = [False]


def _bs():
    import baostock as bs
    return bs


def _ensure_login():
    bs = _bs()
    if not _LOGGED_IN[0]:
        with contextlib.redirect_stdout(io.StringIO()):
            lg = bs.login()
        if lg.error_code != "0":
            log(f"  ⚠ baostock 登录失败: {lg.error_msg}")
            return False
        _LOGGED_IN[0] = True
    return True


def bs_code(code):
    """'300750' → 'sz.300750'；'600519' → 'sh.600519'"""
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    return f"{prefix}.{code}"


def _query(bs_symbol, fields, start_date, end_date, adjustflag="3"):
    bs = _bs()
    with contextlib.redirect_stdout(io.StringIO()):
        rs = bs.query_history_k_data_plus(
            bs_symbol, fields, start_date=start_date, end_date=end_date,
            frequency="d", adjustflag=adjustflag)
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
    if rs.error_code != "0":
        log(f"  ⚠ baostock 查询失败 {bs_symbol}: {rs.error_msg}")
        return None
    return rows


def valuation_history(code, start_date, end_date):
    """日频估值。返回升序 [{date, pe, pb, ps, turnover}] 或 None。"""
    try:
        import baostock  # noqa: F401
    except ImportError:
        log("  ⚠ baostock 未安装，跳过")
        return None
    if not _ensure_login():
        return None
    rows = _query(bs_code(code), "date,peTTM,pbMRQ,psTTM,turn,tradestatus",
                  start_date, end_date)
    if rows is None:
        return None
    out = []
    for r in rows:
        if r[5] != "1":   # 停牌日跳过
            continue
        out.append({
            "date": r[0],
            "pe": float(r[1]) if r[1] else None,
            "pb": float(r[2]) if r[2] else None,
            "ps": float(r[3]) if r[3] else None,
            "turnover": float(r[4]) if r[4] else None,
        })
    return out


def index_kline(bs_symbol, start_date, end_date):
    """指数日K（baostock 符号，如 'sz.399006'）。返回 [{d,o,h,l,c,v,amount}] 或 None。"""
    try:
        import baostock  # noqa: F401
    except ImportError:
        return None
    if not _ensure_login():
        return None
    rows = _query(bs_symbol, "date,open,high,low,close,volume,amount",
                  start_date, end_date)
    if rows is None:
        return None
    out = []
    for r in rows:
        try:
            out.append({
                "d": r[0],
                "o": float(r[1]), "h": float(r[2]),
                "l": float(r[3]), "c": float(r[4]),
                "v": float(r[5]) if r[5] else 0.0,
                "amount": float(r[6]) if r[6] else 0.0,
            })
        except (TypeError, ValueError):
            continue
    return out


def stock_kline(code, start_date, end_date, adjustflag="2"):
    """个股日K。adjustflag: 1=后复权 2=前复权 3=不复权。
    返回 [{d,o,h,l,c,v,amount}] 或 None。
    （fuyao 的 adjust=forward 实测存在漂移误差：2019-2021 复权因子应为常数但呈渐变，改用 baostock 事件式复权。）"""
    try:
        import baostock  # noqa: F401
    except ImportError:
        return None
    if not _ensure_login():
        return None
    rows = _query(bs_code(code), "date,open,high,low,close,volume,amount",
                  start_date, end_date, adjustflag)
    if rows is None:
        return None
    out = []
    for r in rows:
        try:
            out.append({
                "d": r[0],
                "o": float(r[1]), "h": float(r[2]),
                "l": float(r[3]), "c": float(r[4]),
                "v": float(r[5]) if r[5] else 0.0,
                "amount": float(r[6]) if r[6] else 0.0,
            })
        except (TypeError, ValueError):
            continue
    return out


def logout():
    if _LOGGED_IN[0]:
        bs = _bs()
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()
        _LOGGED_IN[0] = False
