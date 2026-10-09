"""巨潮资讯网：公告列表查询（动态 orgId + seDate 区间分页）

移植自 a-stock-data skill §7.1。无鉴权。
公告详情 URL：https://www.cninfo.com.cn/new/disclosure/detail?stockCode={code}&announcementId={id}&orgId={org}&announceTime={date}
"""

import time

import requests

from common import log

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
STOCK_MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"

_HEADERS = {
    "User-Agent": UA,
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.cninfo.com.cn/new/disclosure",
    "Origin": "https://www.cninfo.com.cn",
}

_ORGID_MAP = {}


def _get_prefix(code):
    if code.startswith(("60", "68", "51", "58", "90")):
        return "s"
    return "z"


def _orgid(code):
    """动态查真实 orgId（巨潮并非统一 gssz0{code} 格式），失败回退硬编码规则。"""
    global _ORGID_MAP
    if not _ORGID_MAP:
        try:
            r = requests.get(STOCK_MAP_URL, headers={"User-Agent": UA}, timeout=15)
            _ORGID_MAP = {s["code"]: s["orgId"]
                          for s in r.json().get("stockList", []) if s.get("code")}
        except Exception as e:
            log(f"  ⚠ 巨潮 orgId 映射表拉取失败，回退硬编码: {e}")
    org = _ORGID_MAP.get(code)
    if org:
        return org
    return f"gs{_get_prefix(code)}0{code}"


def _norm_date(item):
    ms = item.get("announcementTime") or 0
    if not ms:
        return ""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ms / 1000.0))
    except (OSError, ValueError):
        return ""


def query_announcements(code, se_date, page_size=30, max_pages=60):
    """查询 [se_date] 区间公告（se_date 格式 'YYYY-MM-DD~YYYY-MM-DD'，可为空=不限）。
    返回按时间升序的 [{title, type, date, url, pdf_url}]。
    注意：实测 pageSize>30 时分页失效（pageNum 被忽略、每页返回相同 30 条），故强制 30。"""
    page_size = min(page_size, 30)
    org_id = _orgid(code)
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        payload = {
            "stock": f"{code},{org_id}",
            "tabName": "fulltext",
            "pageSize": str(page_size),
            "pageNum": str(page),
            "column": "", "category": "", "plate": "",
            "seDate": se_date or "", "searchkey": "", "secid": "",
            "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        try:
            r = requests.post(QUERY_URL, data=payload, headers=_HEADERS, timeout=20)
            d = r.json()
        except Exception:
            time.sleep(0.5)
            continue
        items = d.get("announcements") or []
        if not items:
            break
        for a in items:
            ann_id = a.get("announcementId", "")
            date = _norm_date(a)
            dedup_key = (ann_id, a.get("announcementTitle", ""))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            adj = a.get("adjunctUrl") or ""
            out.append({
                "title": (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", ""),
                "type": a.get("announcementTypeName", "") or "",
                "date": date,
                "url": (f"https://www.cninfo.com.cn/new/disclosure/detail"
                        f"?stockCode={code}&announcementId={ann_id}&orgId={org_id}"
                        f"&announceTime={date.replace('-', '')}") if ann_id else "",
                "pdf_url": f"https://static.cninfo.com.cn/{adj}" if adj else "",
            })
        if not d.get("hasMore"):
            break
        time.sleep(0.2)
    out.sort(key=lambda x: x["date"])
    return out
