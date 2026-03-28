#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared tender scraping logic for domestic AGV/GPU reports."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import os
import re
import smtplib
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised via runtime environment
    requests = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - exercised via runtime environment
    BeautifulSoup = None  # type: ignore[assignment]


DATE_PATTERNS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
]

SMTP_CONFIGS = {
    "qq.com": {"server": "smtp.qq.com", "port": 465, "ssl": True},
    "foxmail.com": {"server": "smtp.qq.com", "port": 465, "ssl": True},
    "163.com": {"server": "smtp.163.com", "port": 465, "ssl": True},
    "126.com": {"server": "smtp.126.com", "port": 465, "ssl": True},
    "gmail.com": {"server": "smtp.gmail.com", "port": 587, "ssl": False},
    "outlook.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "hotmail.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "live.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "sina.com": {"server": "smtp.sina.com", "port": 465, "ssl": True},
    "sohu.com": {"server": "smtp.sohu.com", "port": 465, "ssl": True},
    "aliyun.com": {"server": "smtp.aliyun.com", "port": 465, "ssl": True},
    "139.com": {"server": "smtp.139.com", "port": 465, "ssl": True},
}

DEPRECATED_OUTPUT_DEFAULT = "agv_tenders_last_week.xlsx"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
KEYWORD_INPUT_RE = re.compile(r"(keyword|search|query|key|q)", re.IGNORECASE)
NEXT_PAGE_TEXTS = {"下一页", "下页", ">", ">>"}
SPECIAL_FETCHERS = {"ccgp", "cebpub", "cebpubservice", "365trade"}
DISCOVERED_SEARCH_CACHE: Dict[str, Optional["DiscoveredSearch"]] = {}
DISCOVERY_FAILURE_LOGGED = set()


@dataclass(frozen=True)
class TenderItem:
    source: str
    title: str
    publish_date: date
    url: str


@dataclass(frozen=True)
class SiteConfig:
    key: str
    name: str
    search_url: str
    keyword_param: str = "keyword"
    page_param: Optional[str] = None
    base_url: Optional[str] = None
    extra_params: Dict[str, str] = field(default_factory=dict)
    auto_discover: bool = False
    request_method: str = "get"


@dataclass(frozen=True)
class DiscoveredSearch:
    search_url: str
    keyword_param: str
    extra_params: Dict[str, str]
    method: str = "get"


@dataclass(frozen=True)
class ReportProfile:
    key: str
    label: str
    default_keywords: Tuple[str, ...]
    default_sites: Tuple[str, ...]
    default_subject_prefix: str


def ensure_fetch_dependencies() -> None:
    missing = []
    if requests is None:
        missing.append("requests")
    if BeautifulSoup is None:
        missing.append("beautifulsoup4")
    if missing:
        raise RuntimeError(
            "缺少抓取依赖：请先执行 `pip install -r requirements.txt` 安装 "
            + ", ".join(missing)
            + "。"
        )


def create_session() -> "requests.Session":
    ensure_fetch_dependencies()
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    session.trust_env = use_env_proxy()
    return session


def use_env_proxy() -> bool:
    return os.getenv("TENDER_USE_ENV_PROXY", "").strip().lower() in {"1", "true", "yes"}


def parse_date(text: str) -> Optional[date]:
    match = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", text)
    if not match:
        return None
    raw = match.group(1)
    for fmt in DATE_PATTERNS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def in_range(value: Optional[date], start: date, end: date) -> bool:
    return value is not None and start <= value <= end


def normalize_url(base_url: Optional[str], value: str) -> str:
    if not base_url:
        return value
    return urljoin(base_url, value)


def parse_keywords_argument(raw_value: str) -> List[str]:
    values: List[str] = []
    seen = set()
    for chunk in raw_value.split(","):
        keyword = chunk.strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        values.append(keyword)
    return values


def keywords_text(keywords: Sequence[str]) -> str:
    return ", ".join(keywords)


def normalize_match_text(text: str) -> str:
    return re.sub(r"[\s\u3000\-_/\\|,:;，。；：()（）\[\]【】<>《》]", "", text).lower()


def title_matches_keyword(title: str, keyword: str) -> bool:
    title_lower = title.lower()
    keyword_lower = keyword.lower()
    if keyword_lower in title_lower:
        return True
    return normalize_match_text(keyword) in normalize_match_text(title)


def discover_search_form(session: "requests.Session", site: SiteConfig) -> Optional[DiscoveredSearch]:
    ensure_fetch_dependencies()
    try:
        resp = session.get(site.search_url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    for form in soup.find_all("form"):
        method = (form.get("method") or "get").lower()
        if method not in {"get", "post"}:
            continue

        keyword_input = None
        extra_params: Dict[str, str] = {}
        fields = form.find_all(["input", "textarea"])
        for field_tag in fields:
            name = field_tag.get("name")
            if not name:
                continue
            input_type = (field_tag.get("type") or "text").lower()
            if input_type in {"hidden", "submit", "button"}:
                value = field_tag.get("value")
                if value:
                    extra_params[name] = value
                continue
            if KEYWORD_INPUT_RE.search(name):
                keyword_input = name

        if not keyword_input:
            continue

        action = form.get("action") or site.search_url
        search_url = urljoin(site.search_url, action)
        return DiscoveredSearch(
            search_url=search_url,
            keyword_param=keyword_input,
            extra_params=extra_params,
            method=method,
        )
    return None


def collect_context_texts(anchor: object) -> List[str]:
    texts: List[str] = []
    get_text = getattr(anchor, "get_text", None)
    if callable(get_text):
        title_text = get_text(" ", strip=True)
        if title_text:
            texts.append(title_text)

    parent = getattr(anchor, "parent", None)
    if parent is not None:
        parent_text = getattr(parent, "get_text", lambda *args, **kwargs: "")(" ", strip=True)
        if parent_text:
            texts.append(parent_text)

    previous_sibling = getattr(anchor, "previous_sibling", None)
    if previous_sibling is not None:
        sibling_text = str(previous_sibling).strip()
        if sibling_text:
            texts.append(sibling_text)

    next_sibling = getattr(anchor, "next_sibling", None)
    if next_sibling is not None:
        sibling_text = str(next_sibling).strip()
        if sibling_text:
            texts.append(sibling_text)

    return texts


def find_date_from_texts(texts: Sequence[str]) -> Optional[date]:
    for text in texts:
        parsed = parse_date(text)
        if parsed:
            return parsed
    return None


def extract_items_from_anchors(
    *,
    anchors: Iterable[object],
    source: str,
    keyword: str,
    start: date,
    end: date,
    base_url: str,
) -> List[TenderItem]:
    items: List[TenderItem] = []
    seen_urls = set()
    for anchor in anchors:
        href = getattr(anchor, "get", lambda *_args, **_kwargs: None)("href")
        if not href:
            continue
        title = getattr(anchor, "get", lambda *_args, **_kwargs: None)("title")
        if not title:
            title = getattr(anchor, "get_text", lambda *args, **kwargs: "")(" ", strip=True)
        title = str(title).strip()
        if not title:
            continue
        if keyword and not title_matches_keyword(title, keyword):
            continue

        publish_date = find_date_from_texts(collect_context_texts(anchor))
        if not in_range(publish_date, start, end):
            continue

        url = normalize_url(base_url, str(href))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        items.append(
            TenderItem(
                source=source,
                title=title,
                publish_date=publish_date,
                url=url,
            )
        )
    return items


def find_next_page_url(soup: object, current_url: str) -> Optional[str]:
    select = getattr(soup, "select", None)
    if not callable(select):
        return None

    for anchor in select("a[href]"):
        href = anchor.get("href")
        if not href:
            continue
        href = str(href).strip()
        if not href or href.startswith("javascript:"):
            continue
        text = anchor.get_text(" ", strip=True)
        classes = " ".join(anchor.get("class") or [])
        rels = " ".join(anchor.get("rel") or [])
        if text in NEXT_PAGE_TEXTS or "next" in classes.lower() or "next" in rels.lower():
            return normalize_url(current_url, href)
    return None


def fetch_ccgp(keyword: str, start: date, end: date, max_pages: int = 3) -> List[TenderItem]:
    items: List[TenderItem] = []
    session = create_session()

    base_url = "https://search.ccgp.gov.cn/bxsearch"
    for page in range(1, max_pages + 1):
        params = {
            "searchtype": "2",
            "page_index": str(page),
            "kw": keyword,
            "timeType": "6",
            "start_time": start.strftime("%Y:%m:%d"),
            "end_time": end.strftime("%Y:%m:%d"),
            "dbselect": "bidx",
        }
        try:
            resp = session.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[中国政府采购网] request failed: {exc}", file=sys.stderr)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        containers = soup.select("#result_list li") or soup.select("li")
        page_items = extract_items_from_anchors(
            anchors=(li.find("a", href=True) for li in containers),
            source="中国政府采购网",
            keyword=keyword,
            start=start,
            end=end,
            base_url=base_url,
        )
        items.extend(page_items)
    return items


def fetch_cebpubservice_html(
    keyword: str, start: date, end: date, max_pages: int = 3
) -> List[TenderItem]:
    items: List[TenderItem] = []
    session = create_session()

    base_url = "https://bulletin.cebpubservice.com/xxfbcmses/search/bulletin.html"
    days_range = max(1, (end - start).days)
    for page in range(1, max_pages + 1):
        params = {
            "searchDate": end.strftime("%Y-%m-%d"),
            "dates": str(days_range),
            "categoryId": "88",
            "showStatus": "1",
            "word": keyword,
            "page": str(page),
        }
        try:
            resp = session.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[中国招标投标公共服务平台] request failed: {exc}", file=sys.stderr)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        for row in soup.select("table tr"):
            link = row.find("a", href=True)
            if not link:
                continue
            title = link.get("title") or link.get_text(strip=True)
            if not title:
                continue
            if not title_matches_keyword(title, keyword):
                continue
            publish_date = parse_date(row.get_text(" ", strip=True))
            if not in_range(publish_date, start, end):
                continue

            url = link["href"]
            match = re.search(r"urlOpen\('([^']+)'\)", url)
            if match:
                url = f"https://ctbpsp.com/#/bulletinDetail?uuid={match.group(1)}"
            elif url.startswith("/"):
                url = "https://bulletin.cebpubservice.com" + url

            items.append(
                TenderItem(
                    source="中国招标投标公共服务平台",
                    title=title,
                    publish_date=publish_date,
                    url=url,
                )
            )
    return items


def fetch_cebpubservice_www(
    keyword: str, start: date, end: date, max_pages: int = 3
) -> List[TenderItem]:
    items: List[TenderItem] = []
    session = create_session()
    session.headers.update(
        {
            "Referer": (
                "http://www.cebpubservice.com/ctpsp_iiss/"
                "searchbusinesstypebeforedooraction/getSearch.do"
            )
        }
    )

    endpoint = (
        "http://www.cebpubservice.com/"
        "ctpsp_iiss/searchbusinesstypebeforedooraction/getStringMethod.do"
    )
    stop_date = end + timedelta(days=1)
    rows_per_page = 15

    for page in range(1, max_pages + 1):
        payload = {
            "searchName": keyword,
            "businessType": "招标公告",
            "bulletinIssnTimeStart": start.strftime("%Y-%m-%d"),
            "bulletinIssnTimeStop": stop_date.strftime("%Y-%m-%d"),
            "pageNo": str(page),
            "row": str(rows_per_page),
        }
        try:
            resp = session.post(endpoint, data=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"[中国招标投标公共服务平台（www）] request failed: {exc}", file=sys.stderr)
            break

        data_object = data.get("object") if isinstance(data, dict) else None
        if not isinstance(data_object, dict):
            break

        result_rows = data_object.get("returnlist")
        if not isinstance(result_rows, list):
            break

        for row in result_rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("businessObjectName") or "").strip()
            if not title:
                continue
            if not title_matches_keyword(title, keyword):
                continue
            publish_date = parse_date(str(row.get("receiveTime") or ""))
            if not in_range(publish_date, start, end):
                continue

            business_id = str(row.get("businessId") or "").strip()
            if business_id:
                url = f"https://ctbpsp.com/#/bulletinDetail?uuid={business_id}"
            else:
                url = (
                    "http://www.cebpubservice.com/"
                    "ctpsp_iiss/searchbusinesstypebeforedooraction/getSearch.do"
                )

            items.append(
                TenderItem(
                    source="中国招标投标公共服务平台（www）",
                    title=title,
                    publish_date=publish_date,
                    url=url,
                )
            )

        page_info = data_object.get("page")
        total_page = 0
        if isinstance(page_info, dict):
            try:
                total_page = int(page_info.get("totalPage") or 0)
            except (TypeError, ValueError):
                total_page = 0
        if total_page and page >= total_page:
            break
    return items


def parse_365trade_page(
    html_text: str, keyword: str, start: date, end: date, current_url: str
) -> Tuple[List[TenderItem], Optional[str]]:
    ensure_fetch_dependencies()
    soup = BeautifulSoup(html_text, "lxml")
    selectors = [
        ".search-result li a[href]",
        ".search_list li a[href]",
        ".ewb-search-items a[href]",
        ".list li a[href]",
        "li a[href]",
    ]

    anchors = []
    seen_href = set()
    for selector in selectors:
        for anchor in soup.select(selector):
            href = anchor.get("href")
            if not href or href in seen_href:
                continue
            seen_href.add(href)
            anchors.append(anchor)

    items = extract_items_from_anchors(
        anchors=anchors,
        source="中招联合招标采购平台",
        keyword=keyword,
        start=start,
        end=end,
        base_url=current_url,
    )
    return items, find_next_page_url(soup, current_url)


def fetch_365trade(keyword: str, start: date, end: date, max_pages: int = 3) -> List[TenderItem]:
    items: List[TenderItem] = []
    session = create_session()
    current_url = "https://www.365trade.com.cn/search_156.jspx"
    seen_pages = set()

    for page in range(1, max_pages + 1):
        if current_url in seen_pages:
            break
        seen_pages.add(current_url)

        try:
            if page == 1:
                resp = session.get(current_url, params={"q": keyword}, timeout=30)
            else:
                resp = session.get(current_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[中招联合招标采购平台] request failed: {exc}", file=sys.stderr)
            break

        page_items, next_url = parse_365trade_page(resp.text, keyword, start, end, resp.url)
        items.extend(page_items)
        if not next_url:
            break
        current_url = next_url
    return items


def fetch_generic_site(
    site: SiteConfig, keyword: str, start: date, end: date, max_pages: int = 3
) -> List[TenderItem]:
    items: List[TenderItem] = []
    session = create_session()

    search_url = site.search_url
    keyword_param = site.keyword_param
    extra_params = dict(site.extra_params)
    method = site.request_method.lower()
    if site.auto_discover:
        if site.key not in DISCOVERED_SEARCH_CACHE:
            DISCOVERED_SEARCH_CACHE[site.key] = discover_search_form(session, site)
        discovered = DISCOVERED_SEARCH_CACHE[site.key]
        if discovered:
            search_url = discovered.search_url
            keyword_param = discovered.keyword_param
            extra_params.update(discovered.extra_params)
            method = discovered.method
        else:
            if site.key not in DISCOVERY_FAILURE_LOGGED:
                DISCOVERY_FAILURE_LOGGED.add(site.key)
                print(
                    f"[{site.name}] search form discovery failed; skipping site.",
                    file=sys.stderr,
                )
            return items

    for page in range(1, max_pages + 1):
        payload = dict(extra_params)
        payload[keyword_param] = keyword
        if site.page_param:
            payload[site.page_param] = str(page)

        try:
            if method == "post":
                resp = session.post(search_url, data=payload, timeout=30)
            else:
                resp = session.get(search_url, params=payload, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[{site.name}] request failed: {exc}", file=sys.stderr)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        page_items = extract_items_from_anchors(
            anchors=soup.select("a[href]"),
            source=site.name,
            keyword=keyword,
            start=start,
            end=end,
            base_url=site.base_url or search_url,
        )
        if not page_items and page > 1:
            break
        items.extend(page_items)
    return items


def fetch_site_items(
    site: SiteConfig, keyword: str, start: date, end: date, max_pages: int
) -> List[TenderItem]:
    if site.key == "ccgp":
        return fetch_ccgp(keyword, start, end, max_pages)
    if site.key == "cebpub":
        return fetch_cebpubservice_html(keyword, start, end, max_pages)
    if site.key == "cebpubservice":
        return fetch_cebpubservice_www(keyword, start, end, max_pages)
    if site.key == "365trade":
        return fetch_365trade(keyword, start, end, max_pages)
    return fetch_generic_site(site, keyword, start, end, max_pages)


def dedupe(items: Iterable[TenderItem]) -> List[TenderItem]:
    seen = set()
    result = []
    for item in items:
        if item.url in seen:
            continue
        seen.add(item.url)
        result.append(item)
    return result


def parse_receivers(raw_value: str, sender: str) -> List[str]:
    receivers = [item.strip() for item in raw_value.split(",") if item.strip()]
    if receivers:
        return receivers
    if sender:
        return [sender]
    return []


def resolve_smtp(sender: str) -> Dict[str, object]:
    domain = sender.split("@")[-1].lower() if "@" in sender else ""
    smtp_config = SMTP_CONFIGS.get(domain)
    if smtp_config:
        return smtp_config
    return {"server": f"smtp.{domain}", "port": 465, "ssl": True}


def build_email_content(
    items: List[TenderItem],
    profile: ReportProfile,
    keywords: Sequence[str],
    start_date: date,
    end_date: date,
) -> Dict[str, str]:
    keyword_label = keywords_text(keywords)
    report_title = f"{profile.label} 招标信息简报（关键词: {keyword_label}）"
    header = [
        report_title,
        f"时间范围: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}",
        f"结果数量: {len(items)}",
        "",
    ]
    lines = list(header)
    if not items:
        lines.append("本周期未查询到新增招标信息。")
    else:
        for index, item in enumerate(items, start=1):
            lines.extend(
                [
                    f"{index}. [{item.publish_date.strftime('%Y-%m-%d')}] {item.source}",
                    f"   标题: {item.title}",
                    f"   链接: {item.url}",
                    "",
                ]
            )

    plain_text = "\n".join(lines).strip()

    html_rows = []
    for item in items:
        html_rows.append(
            "<tr>"
            f"<td>{html.escape(item.publish_date.strftime('%Y-%m-%d'))}</td>"
            f"<td>{html.escape(item.source)}</td>"
            f"<td>{html.escape(item.title)}</td>"
            f"<td><a href=\"{html.escape(item.url)}\">查看详情</a></td>"
            "</tr>"
        )

    if html_rows:
        table_html = (
            "<table border='1' cellspacing='0' cellpadding='6'>"
            "<thead><tr><th>日期</th><th>来源</th><th>标题</th><th>链接</th></tr></thead>"
            f"<tbody>{''.join(html_rows)}</tbody></table>"
        )
    else:
        table_html = "<p>本周期未查询到新增招标信息。</p>"

    html_body = (
        "<html><body>"
        f"<p><strong>{html.escape(report_title)}</strong></p>"
        f"<p>时间范围: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}<br>"
        f"结果数量: {len(items)}</p>"
        f"{table_html}"
        "</body></html>"
    )
    return {"text": plain_text, "html": html_body}


def send_email_report(
    *,
    sender: str,
    password: str,
    receivers: List[str],
    subject: str,
    plain_text: str,
    html_body: str,
) -> bool:
    if not sender or not password or not receivers:
        return False

    smtp_config = resolve_smtp(sender)
    smtp_server = str(smtp_config["server"])
    smtp_port = int(smtp_config["port"])
    use_ssl = bool(smtp_config["ssl"])

    message = MIMEMultipart("alternative")
    message["Subject"] = Header(subject, "utf-8")
    message["From"] = sender
    message["To"] = ", ".join(receivers)
    message.attach(MIMEText(plain_text, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            server.starttls()
        server.login(sender, password)
        server.send_message(message)
        server.quit()
        return True
    except Exception as exc:  # pragma: no cover - depends on external SMTP
        print(f"[邮件] send failed: {exc}", file=sys.stderr)
        return False


def build_site_registry() -> List[SiteConfig]:
    return [
        SiteConfig(
            key="ccgp",
            name="中国政府采购网",
            search_url="https://search.ccgp.gov.cn/bxsearch",
            keyword_param="kw",
            page_param="page_index",
            extra_params={
                "searchtype": "2",
                "timeType": "6",
                "dbselect": "bidx",
            },
        ),
        SiteConfig(
            key="ggzy",
            name="全国公共资源交易平台",
            search_url="https://www.ggzy.gov.cn/",
            keyword_param="searchword",
            auto_discover=True,
        ),
        SiteConfig(
            key="zcygov",
            name="政采云",
            search_url="https://www.zcygov.cn/",
            auto_discover=True,
        ),
        SiteConfig(
            key="zycg",
            name="中央政府采购网",
            search_url="https://www.zycg.gov.cn/",
            auto_discover=True,
        ),
        SiteConfig(
            key="szecp",
            name="深圳电子采购平台",
            search_url="https://www.szecp.com.cn/",
            auto_discover=True,
        ),
        SiteConfig(
            key="cebpub",
            name="中国招标投标公共服务平台",
            search_url="https://bulletin.cebpubservice.com/xxfbcmses/search/bulletin.html",
            keyword_param="word",
            page_param="page",
            base_url="https://bulletin.cebpubservice.com",
        ),
        SiteConfig(
            key="cebpubservice",
            name="中国招标投标公共服务平台（www）",
            search_url=(
                "http://www.cebpubservice.com/ctpsp_iiss/"
                "searchbusinesstypebeforedooraction/getSearch.do"
            ),
            keyword_param="searchName",
            base_url="http://www.cebpubservice.com",
        ),
        SiteConfig(
            key="mofcom",
            name="中国国际招标网（商务部）",
            search_url="https://chinabidding.mofcom.gov.cn/",
            auto_discover=True,
        ),
        SiteConfig(
            key="epec",
            name="中国石化物资电子招投标",
            search_url="https://bidding.epec.com/",
            auto_discover=True,
        ),
        SiteConfig(
            key="sinopec-ebid",
            name="中国石化建设工程电子招标投标",
            search_url="https://ebidding.sinopec.com/",
            auto_discover=True,
        ),
        SiteConfig(
            key="cnpcbidding",
            name="中国石油招标投标网",
            search_url="https://www.cnpcbidding.com/",
            auto_discover=True,
        ),
        SiteConfig(
            key="crecgec",
            name="中国中铁（鲁班等）",
            search_url="https://www.crecgec.com/",
            auto_discover=True,
        ),
        SiteConfig(
            key="crcchc",
            name="铁建汇采",
            search_url="https://www.crcchc.com/",
            auto_discover=True,
        ),
        SiteConfig(
            key="e-bidding",
            name="国信e采",
            search_url="https://www.e-bidding.org/",
            auto_discover=True,
        ),
        SiteConfig(
            key="tobacco",
            name="中烟电子采购平台",
            search_url="https://cgjy.tobacco.com.cn/",
            auto_discover=True,
        ),
        SiteConfig(
            key="ebnew",
            name="必联网",
            search_url="https://www.ebnew.com/",
            auto_discover=True,
        ),
        SiteConfig(
            key="bidcenter",
            name="采招网（bidcenter）",
            search_url="https://www.bidcenter.com.cn/zbxx/",
            keyword_param="search",
        ),
        SiteConfig(
            key="chinabidding",
            name="中国采购与招标网",
            search_url="https://www.chinabidding.com.cn/",
            auto_discover=True,
        ),
        SiteConfig(
            key="365trade",
            name="中招联合招标采购平台",
            search_url="https://www.365trade.com.cn/search_156.jspx",
            keyword_param="q",
            base_url="https://www.365trade.com.cn",
        ),
        SiteConfig(
            key="ecsg",
            name="南方电网电子采购交易平台",
            search_url="https://ecsg.com.cn/",
            auto_discover=True,
        ),
        SiteConfig(
            key="ygcgfw",
            name="阳光采购服务平台",
            search_url="https://www.ygcgfw.com/",
            auto_discover=True,
        ),
    ]


def build_site_map() -> Dict[str, SiteConfig]:
    return {site.key: site for site in build_site_registry()}


def list_sites() -> List[str]:
    return [f"{site.key}: {site.name}" for site in build_site_registry()]


def default_sites_value(profile: ReportProfile) -> str:
    if profile.default_sites == ("all",):
        return "all"
    return ",".join(profile.default_sites)


def build_parser(profile: ReportProfile) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{profile.label} 招标信息采集")
    parser.add_argument("--keyword", default="", help="单个搜索关键词（兼容旧参数）")
    parser.add_argument("--keywords", default="", help="多个搜索关键词，逗号分隔")
    parser.add_argument("--days", type=int, default=7, help="最近多少天")
    parser.add_argument("--max-pages", type=int, default=3, help="每站点最多页数")
    parser.add_argument(
        "--sites",
        default=default_sites_value(profile),
        help="站点 key 列表，逗号分隔；使用 all 表示全部内置国内站点",
    )
    parser.add_argument("--list-sites", action="store_true", help="列出站点 key")
    parser.add_argument("--email-sender", default=os.getenv("EMAIL_SENDER", ""), help="发件人邮箱")
    parser.add_argument(
        "--email-password",
        default=os.getenv("EMAIL_PASSWORD", ""),
        help="邮箱密码或授权码",
    )
    parser.add_argument(
        "--email-receivers",
        default=os.getenv("EMAIL_RECEIVERS", ""),
        help="收件人列表，逗号分隔；为空则默认发给发件人",
    )
    parser.add_argument("--email-subject", default="", help="自定义邮件主题")
    parser.add_argument("--dry-run", action="store_true", help="仅预览邮件内容，不实际发送")
    parser.add_argument(
        "--output",
        default=DEPRECATED_OUTPUT_DEFAULT,
        help="已弃用参数，不再生成 Excel",
    )
    return parser


def resolve_keywords(args: argparse.Namespace, profile: ReportProfile) -> List[str]:
    if args.keywords.strip():
        return parse_keywords_argument(args.keywords)
    if args.keyword.strip():
        return [args.keyword.strip()]
    return list(profile.default_keywords)


def resolve_sites(raw_value: str, site_map: Dict[str, SiteConfig]) -> List[SiteConfig]:
    if raw_value == "all":
        return list(site_map.values())

    selected_sites = []
    for key in (item.strip() for item in raw_value.split(",")):
        if not key:
            continue
        site = site_map.get(key)
        if not site:
            print(f"Unknown site key: {key}", file=sys.stderr)
            continue
        selected_sites.append(site)
    return selected_sites


def build_subject(
    profile: ReportProfile,
    custom_subject: str,
    end_date: date,
    item_count: int,
) -> str:
    if custom_subject.strip():
        return custom_subject.strip()
    return f"{profile.default_subject_prefix} {end_date.strftime('%Y-%m-%d')}（{item_count}条）"


def run_profile(profile: ReportProfile) -> int:
    parser = build_parser(profile)
    args = parser.parse_args()

    sites = build_site_registry()
    site_map = {site.key: site for site in sites}
    if args.list_sites:
        for site in sites:
            print(f"{site.key}: {site.name}")
        return 0

    keywords = resolve_keywords(args, profile)
    if not keywords:
        print("至少需要一个有效关键词。", file=sys.stderr)
        return 1

    selected_sites = resolve_sites(args.sites, site_map)
    if not selected_sites:
        print("未选择有效站点。", file=sys.stderr)
        return 1

    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)

    all_items: List[TenderItem] = []
    try:
        for site in selected_sites:
            for keyword in keywords:
                all_items.extend(fetch_site_items(site, keyword, start_date, end_date, args.max_pages))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    all_items = dedupe(all_items)
    all_items.sort(key=lambda item: (item.publish_date, item.source), reverse=True)
    content = build_email_content(all_items, profile, keywords, start_date, end_date)

    sender = args.email_sender.strip()
    password = args.email_password.strip()
    receivers = parse_receivers(args.email_receivers, sender)
    if args.output != DEPRECATED_OUTPUT_DEFAULT:
        print("[提示] --output 已弃用，脚本将直接发送邮件。", file=sys.stderr)

    subject = build_subject(profile, args.email_subject, end_date, len(all_items))
    if args.dry_run:
        print(f"[DRY RUN] Subject: {subject}")
        print(content["text"])
        return 0

    if not sender or not password:
        print(
            "邮件配置缺失：请通过 --email-sender/--email-password 或环境变量 "
            "EMAIL_SENDER/EMAIL_PASSWORD 提供发件配置。",
            file=sys.stderr,
        )
        return 1
    if not receivers:
        print("邮件配置缺失：未提供有效收件人。", file=sys.stderr)
        return 1

    sent = send_email_report(
        sender=sender,
        password=password,
        receivers=receivers,
        subject=subject,
        plain_text=content["text"],
        html_body=content["html"],
    )
    if not sent:
        return 1

    print(f"Email sent to {', '.join(receivers)} with {len(all_items)} items.")
    return 0
