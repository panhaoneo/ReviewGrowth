"""东方财富：机构研报列表 / 研报 PDF 下载 / 个股新闻

代码移植自 a-stock-data skill（含限流：请求间隔 >=1s + 抖动，429/5xx 指数退避，403 不重试）。
"""

import json
import random
import re
import time
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
NEWS_API = "https://search-api-web.eastmoney.com/search/jsonp"

EM_MIN_INTERVAL = 1.0
_SESSION = requests.Session()
_last_call = [0.0]


def em_get(url, params=None, headers=None, timeout=30, retries=3):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    for i in range(retries + 1):
        wait = EM_MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.3))
        try:
            r = _SESSION.get(url, params=params, headers=h, timeout=timeout)
            _last_call[0] = time.time()
            if r.status_code == 200:
                return r
            if r.status_code == 403:
                return r
            if i < retries:
                time.sleep(2 ** i)
                continue
            return r
        except Exception:
            _last_call[0] = time.time()
            if i < retries:
                time.sleep(1.5 * (i + 1))
                continue
    return None


def norm_report(r):
    """东财研报 record → 规范化字段。"""
    def fnum(x):
        try:
            v = float(x)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None
    return {
        "infoCode": r.get("infoCode", ""),
        "title": r.get("title", ""),
        "publish_date": (r.get("publishDate") or "")[:10],
        "org": r.get("orgSName", ""),
        "analyst": r.get("researcher", ""),
        "rating": r.get("emRatingName", ""),
        "last_rating": r.get("lastEmRatingName", ""),
        "target_price": fnum(r.get("indvAimPriceT")) or fnum(r.get("indvAimPriceL")),
        "eps_this": r.get("predictThisYearEps"),
        "eps_next": r.get("predictNextYearEps"),
        "eps_next2": r.get("predictNextTwoYearEps"),
        "industry": r.get("indvInduName", ""),
    }


def eastmoney_reports(code, begin="2018-01-01", end="2023-12-31", max_pages=60):
    """个股研报列表（qType=0）。返回规范化 list（含区间过滤由调用方做）。"""
    all_rows, total = [], None
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": begin, "endTime": end,
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page), "pageNumber": str(page),
        }
        r = em_get(REPORT_API, params=params,
                   headers={"Referer": "https://data.eastmoney.com/"}, timeout=30)
        if r is None or r.status_code != 200:
            break
        try:
            d = r.json()
        except Exception:
            break
        rows = d.get("data") or []
        if total is None:
            total = d.get("TotalPage", 1) or 1
        if not rows:
            break
        all_rows.extend(rows)
        if page >= total:
            break
    return [norm_report(r) for r in all_rows]


def report_url(info_code):
    return f"https://data.eastmoney.com/report/info/{info_code}.html"


def download_pdf(info_code, publish_date, org, title, target_dir):
    """下载研报 PDF（须带 Referer）。成功返回路径，失败 None；已存在直接返回。
    注意：pdf.dfcfw.com 对长连接（keep-alive 会话）会拖入涓流/CLOSE_WAIT 状态，
    故此处刻意使用独立短连接（requests.get 每请求新连接），并单独限速+退避重试。"""
    if not info_code:
        return None
    safe = lambda s: re.sub(r'[\\/:*?"<>|]', "_", s or "")[:60]
    fname = f"{publish_date}_{safe(org)}_{safe(title)}.pdf"
    target = Path(target_dir) / fname
    if target.exists() and target.stat().st_size >= 1024:
        return str(target)
    for attempt in range(3):
        time.sleep(0.6 + random.uniform(0, 0.4))
        try:
            r = requests.get(PDF_TPL.format(info_code=info_code),
                             headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
                             timeout=30)
            if r.status_code == 200 and len(r.content) >= 1024:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(r.content)
                return str(target)
        except Exception:
            pass
        if attempt < 2:
            time.sleep(2 + attempt * 2)
    return None


def eastmoney_stock_news(keyword, page_size=50):
    """东财个股新闻（JSONP）。keyword 可为代码或公司名。返回 [{title,content,time,source,url}]。
    注意：该接口仅覆盖近期新闻，历史回溯能力弱（风控间歇空返回 → []）。"""
    inner = json.dumps({
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                       "pageIndex": 1, "pageSize": page_size,
                                       "preTag": "", "postTag": ""}},
    }, separators=(",", ":"))
    r = em_get(NEWS_API, params={"cb": "jQuery_news", "param": inner},
               headers={"Referer": "https://so.eastmoney.com/"}, timeout=15)
    if r is None or r.status_code != 200:
        return []
    try:
        text = r.text
        d = json.loads(text[text.index("(") + 1:text.rindex(")")])
    except Exception:
        return []
    out = []
    for a in (d.get("result", {}).get("cmsArticleWebOld") or []):
        out.append({
            "title": re.sub(r"<[^>]+>", "", a.get("title", "")),
            "content": re.sub(r"<[^>]+>", "", a.get("content", ""))[:300],
            "time": (a.get("date") or "").replace("-", "-"),
            "source": a.get("mediaName", ""),
            "url": a.get("url", ""),
        })
    return out
