"""新浪财经研报库（后备研报源：东财 reportapi 对部分中小盘历史研报存在数据缺口）

- 列表：http://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml?symbol={code}&t1=all&p={page}
- 正文：//stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/{rptid}/index.phtml
- 每页约 39 条；分页通过 URL 参数 p 翻页；页面 GBK 编码
"""

import re
import time

import requests

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_LIST_URL = "http://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml"
_ROW = re.compile(
    r'<td>(\d+)</td>\s*<td class="tal f14">\s*<a[^>]+href="([^"]+)"[^>]*>\s*(.*?)\s*</a>\s*</td>'
    r'\s*<td>([^<]*)</td>\s*<td>(\d{4}-\d{2}-\d{2})</td>'
    r'\s*<td>(.*?)</td>\s*<td>(.*?)</td>',
    re.S)


def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def short_org(full):
    """'华西证券股份有限公司' → '华西证券'"""
    s = full or ""
    for suf in ("股份有限公司", "有限公司", "有限责任公司"):
        s = s.replace(suf, "")
    return s


def list_reports(code, start_date, end_date, max_pages=15):
    """拉取研报列表并按 [start_date, end_date] 过滤。返回升序 [{date,title,url,rptid,org,analysts,kind}]。"""
    out, stop = [], False
    for p in range(1, max_pages + 1):
        try:
            r = requests.get(_LIST_URL, params={"symbol": code, "t1": "all", "p": p},
                             headers=_UA, timeout=20)
            r.encoding = "gbk"
            rows = _ROW.findall(r.text)
        except Exception:
            time.sleep(1.0)
            continue
        if not rows:
            break
        for _, url, title, kind, date, org_html, analyst_html in rows:
            out.append({
                "date": date,
                "title": _clean(title),
                "url": ("https:" + url) if url.startswith("//") else url,
                "rptid": url.split("/rptid/")[1].split("/")[0] if "/rptid/" in url else "",
                "org": short_org(_clean(org_html)),
                "analysts": _clean(analyst_html),
                "kind": _clean(kind),
            })
        # 按日期降序，若本页最早日期已早于起点，再翻一页即可停止
        page_dates = [x["date"] for x in out[-len(rows):]]
        if page_dates and min(page_dates) < start_date:
            stop = True
        if stop:
            break
        time.sleep(0.35)
    return [x for x in out if start_date <= x["date"] <= end_date]


def fetch_report_text(url, max_len=12000):
    """抓取研报正文页并转换为纯文本（保留业绩/预测/风险等要点段落）。失败返回 None。"""
    try:
        r = requests.get(url, headers=_UA, timeout=20)
        r.encoding = "gbk"
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
        plain = _clean(text.replace("&nbsp;", " "))
        # 正文从「类别：」附近开始，到免责声明/声明区结束
        i = plain.find("类别：")
        if i < 0:
            i = plain.find("投资要点")
        if i < 0:
            i = 0
        end = len(plain)
        for marker in ("免责声明", "新浪声明", "郑重声明", "声明：", "【免责条款】"):
            j = plain.find(marker, i + 50)
            if j > 0:
                end = min(end, j)
        body = plain[i:end].strip()
        return body[:max_len] if len(body) > 80 else None
    except Exception:
        return None
