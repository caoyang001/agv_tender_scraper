#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Search AGV tenders from domestic bidding sites and send email reports."""
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
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


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


@dataclass(frozen=True)
class DiscoveredSearch:
    search_url: str
    keyword_param: str
    extra_params: Dict[str, str]


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


def in_range(d: Optional[date], start: date, end: date) -> bool:
    return d is not None and start <= d <= end


def normalize_url(base_url: Optional[str], url: str) -> str:
    if not base_url:
        return url
    return urljoin(base_url, url)


def discover_search_form(session: requests.Session, site: SiteConfig) -> Optional[DiscoveredSearch]:
    try:
        resp = session.get(site.search_url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    for form in soup.find_all("form"):
        method = (form.get("method") or "get").lower()
        if method != "get":
            continue
        inputs = form.find_all("input")
        keyword_input = None
        extra_params: Dict[str, str] = {}
        for input_tag in inputs:
            name = input_tag.get("name")
            if not name:
                continue
            input_type = (input_tag.get("type") or "text").lower()
            if input_type in {"hidden", "submit"}:
                value = input_tag.get("value")
                if value:
                    extra_params[name] = value
                continue
            if re.search(r"(keyword|search|query|key|q)", name, re.IGNORECASE):
                keyword_input = name
        if not keyword_input:
            continue
        action = form.get("action") or site.search_url
        search_url = urljoin(site.search_url, action)
        return DiscoveredSearch(
            search_url=search_url,
            keyword_param=keyword_input,
            extra_params=extra_params,
        )
    return None


def fetch_ccgp(keyword: str, start: date, end: date, max_pages: int = 3) -> List[TenderItem]:
    """China Government Procurement Network (中国政府采购网)"""
    items: List[TenderItem] = []
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        }
    )

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
        containers = soup.select("#result_list li")
        if not containers:
            containers = soup.select("li")

        for li in containers:
            link = li.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            if not title:
                continue
            publish_date = parse_date(li.get_text(" ", strip=True))
            if not in_range(publish_date, start, end):
                continue
            items.append(
                TenderItem(
                    source="中国政府采购网",
                    title=title,
                    publish_date=publish_date,
                    url=link["href"],
                )
            )
    return items


def fetch_cebpubservice_html(
    keyword: str, start: date, end: date, max_pages: int = 3
) -> List[TenderItem]:
    """China Bidding Public Service Platform (HTML fallback)."""
    items: List[TenderItem] = []
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        }
    )

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
        rows = soup.select("table tr")
        for row in rows:
            link = row.find("a", href=True)
            if not link:
                continue
            title = link.get("title") or link.get_text(strip=True)
            if not title:
                continue
            publish_date = parse_date(row.get_text(" ", strip=True))
            if not in_range(publish_date, start, end):
                continue
            url = link["href"]
            match = re.search(r"urlOpen\('([^']+)'\)", url)
            if match:
                url = f"https://ctbpsp.com/#/bulletinDetail?uuid={match.group(1)}"
            if url.startswith("/"):
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
    """China Bidding Public Service Platform (www.cebpubservice.com)."""
    items: List[TenderItem] = []
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "http://www.cebpubservice.com/ctpsp_iiss/searchbusinesstypebeforedooraction/getSearch.do",
        }
    )

    endpoint = (
        "http://www.cebpubservice.com/"
        "ctpsp_iiss/searchbusinesstypebeforedooraction/getStringMethod.do"
    )
    # The endpoint treats stop date as an exclusive boundary.
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


def fetch_generic_site(
    site: SiteConfig, keyword: str, start: date, end: date, max_pages: int = 3
) -> List[TenderItem]:
    items: List[TenderItem] = []
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        }
    )

    search_url = site.search_url
    keyword_param = site.keyword_param
    extra_params = dict(site.extra_params)
    if site.auto_discover:
        discovered = discover_search_form(session, site)
        if discovered:
            search_url = discovered.search_url
            keyword_param = discovered.keyword_param
            extra_params.update(discovered.extra_params)

    for page in range(1, max_pages + 1):
        params = dict(extra_params)
        params[keyword_param] = keyword
        if site.page_param:
            params[site.page_param] = str(page)

        try:
            resp = session.get(search_url, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[{site.name}] request failed: {exc}", file=sys.stderr)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        anchors = soup.select("a[href]")
        for anchor in anchors:
            title = anchor.get_text(strip=True)
            if not title:
                continue
            if keyword and keyword.lower() not in title.lower():
                continue

            context_texts = [title]
            parent = anchor.find_parent()
            if parent:
                context_texts.append(parent.get_text(" ", strip=True))
            date_value = None
            for text in context_texts:
                date_value = parse_date(text)
                if date_value:
                    break
            if not in_range(date_value, start, end):
                continue

            url = normalize_url(site.base_url or search_url, anchor.get("href", ""))
            if not url:
                continue
            items.append(
                TenderItem(
                    source=site.name,
                    title=title,
                    publish_date=date_value,
                    url=url,
                )
            )
    return items


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


def build_email_content(items: List[TenderItem], keyword: str, start_date: date, end_date: date) -> Dict[str, str]:
    header = [
        f"AGV 招标信息简报（关键词: {keyword}）",
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
        f"<p><strong>AGV 招标信息简报（关键词: {html.escape(keyword)}）</strong></p>"
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
    except Exception as exc:
        print(f"[邮件] send failed: {exc}", file=sys.stderr)
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AGV 招标信息采集")
    parser.add_argument("--keyword", default="AGV", help="搜索关键词")
    parser.add_argument("--days", type=int, default=7, help="最近多少天")
    parser.add_argument("--max-pages", type=int, default=3, help="每站点最多页数")
    parser.add_argument(
        "--sites",
        default="all",
        help="站点 key 列表，逗号分隔；使用 all 表示全部",
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    sites = [
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
            auto_discover=False,
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
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="zycg",
            name="中央政府采购网",
            search_url="https://www.zycg.gov.cn/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="szecp",
            name="深圳电子采购平台",
            search_url="https://www.szecp.com.cn/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="cebpub",
            name="中国招标投标公共服务平台",
            search_url="https://bulletin.cebpubservice.com/xxfbcmses/search/bulletin.html",
            keyword_param="word",
            page_param="page",
            auto_discover=False,
            base_url="https://bulletin.cebpubservice.com",
        ),
        SiteConfig(
            key="cebpubservice",
            name="中国招标投标公共服务平台（www）",
            search_url="http://www.cebpubservice.com/ctpsp_iiss/searchbusinesstypebeforedooraction/getSearch.do",
            keyword_param="searchName",
            auto_discover=False,
            base_url="http://www.cebpubservice.com",
        ),
        SiteConfig(
            key="mofcom",
            name="中国国际招标网（商务部）",
            search_url="https://chinabidding.mofcom.gov.cn/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="epec",
            name="中国石化物资电子招投标",
            search_url="https://bidding.epec.com/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="sinopec-ebid",
            name="中国石化建设工程电子招标投标",
            search_url="https://ebidding.sinopec.com/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="cnpcbidding",
            name="中国石油招标投标网",
            search_url="https://www.cnpcbidding.com/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="crecgec",
            name="中国中铁（鲁班等）",
            search_url="https://www.crecgec.com/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="crcchc",
            name="铁建汇采",
            search_url="https://www.crcchc.com/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="e-bidding",
            name="国信e采",
            search_url="https://www.e-bidding.org/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="tobacco",
            name="中烟电子采购平台",
            search_url="https://cgjy.tobacco.com.cn/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="ebnew",
            name="必联网",
            search_url="https://www.ebnew.com/",
            keyword_param="keyword",
            auto_discover=True,
        ),
        SiteConfig(
            key="bidcenter",
            name="采招网（bidcenter）",
            search_url="https://www.bidcenter.com.cn/zbxx/",
            keyword_param="search",
            auto_discover=False,
        ),
        SiteConfig(
            key="chinabidding",
            name="中国采购与招标网",
            search_url="https://www.chinabidding.com.cn/",
            keyword_param="keyword",
            auto_discover=True,
        ),
    ]

    site_map = {site.key: site for site in sites}
    if args.list_sites:
        for site in sites:
            print(f"{site.key}: {site.name}")
        return

    if args.sites == "all":
        selected_sites = sites
    else:
        selected_sites = []
        for key in (item.strip() for item in args.sites.split(",")):
            if not key:
                continue
            site = site_map.get(key)
            if not site:
                print(f"Unknown site key: {key}", file=sys.stderr)
                continue
            selected_sites.append(site)

    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)

    all_items: List[TenderItem] = []
    for site in selected_sites:
        if site.key == "ccgp":
            all_items.extend(fetch_ccgp(args.keyword, start_date, end_date, args.max_pages))
            continue
        if site.key == "cebpub":
            all_items.extend(
                fetch_cebpubservice_html(args.keyword, start_date, end_date, args.max_pages)
            )
            continue
        if site.key == "cebpubservice":
            all_items.extend(
                fetch_cebpubservice_www(args.keyword, start_date, end_date, args.max_pages)
            )
            continue
        all_items.extend(fetch_generic_site(site, args.keyword, start_date, end_date, args.max_pages))

    all_items = dedupe(all_items)
    all_items.sort(key=lambda item: (item.publish_date, item.source), reverse=True)
    content = build_email_content(all_items, args.keyword, start_date, end_date)

    sender = args.email_sender.strip()
    password = args.email_password.strip()
    receivers = parse_receivers(args.email_receivers, sender)
    if args.output != DEPRECATED_OUTPUT_DEFAULT:
        print("[提示] --output 已弃用，脚本将直接发送邮件。", file=sys.stderr)

    date_str = end_date.strftime("%Y-%m-%d")
    if args.email_subject.strip():
        subject = args.email_subject.strip()
    else:
        subject = f"AGV 招标信息简报 {date_str}（{len(all_items)}条）"

    if args.dry_run:
        print(f"[DRY RUN] Subject: {subject}")
        print(content["text"])
        return

    if not sender or not password:
        print(
            "邮件配置缺失：请通过 --email-sender/--email-password 或环境变量 "
            "EMAIL_SENDER/EMAIL_PASSWORD 提供发件配置。",
            file=sys.stderr,
        )
        sys.exit(1)
    if not receivers:
        print("邮件配置缺失：未提供有效收件人。", file=sys.stderr)
        sys.exit(1)

    sent = send_email_report(
        sender=sender,
        password=password,
        receivers=receivers,
        subject=subject,
        plain_text=content["text"],
        html_body=content["html"],
    )
    if not sent:
        sys.exit(1)

    print(f"Email sent to {', '.join(receivers)} with {len(all_items)} items.")


if __name__ == "__main__":
    main()
