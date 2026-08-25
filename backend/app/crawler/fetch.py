# 抓取器（P2）：UA 标识、按主机限速、xlsx 附件发现与下载。
# 合规（设计文档 §6.3）：只抓公开公告页；限速默认 ≥10s/主机；失败降级不中断。
from __future__ import annotations

import re
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

_UA = (
    "govjob-radar/0.1 (personal job-hunting tool; +https://github.com/you/govjob-radar)"
)
_MIN_INTERVAL = 10.0  # 同主机两次请求最小间隔（秒）
_last_hit: Dict[str, float] = {}

_XLSX_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+?\.(?:xlsx|xls))["']""", re.I)
_HREF_RE = re.compile(r"""<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ANNOUNCEMENT_RE = re.compile(
    r"招聘|招录|招考|公务员|公开招聘|选调|引进|遴选|文职|聘用|三支一扶|西部计划|特岗|社区工作者|辅导员"
)


def _polite_wait(host: str) -> None:
    now = time.time()
    elapsed = now - _last_hit.get(host, 0.0)
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_hit[host] = time.time()


def fetch_url(url: str, timeout: int = 20) -> bytes:
    from urllib.parse import urlparse

    _polite_wait(urlparse(url).netloc)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def discover_xlsx_links(page_url: str, html: bytes) -> List[str]:
    """从公告列表/详情页 HTML 里找 xlsx 附件链接（绝对化）。"""
    try:
        text = html.decode("utf-8", errors="ignore")
    except Exception:
        return []
    links = []
    for m in _XLSX_HREF_RE.finditer(text):
        absu = urljoin(page_url, m.group(1))
        if absu not in links:
            links.append(absu)
    return links


def discover_source_xlsx_links(source, notice_limit: int = 3) -> List[str]:
    """发现 A 类职位表。

    入口页通常只列公告、职位表挂在详情页；先查入口直链，未命中时再跟进少量
    含招考关键词的公告页。只返回链接，不下载，方便调用方保留现有下载限额。
    """
    if not source.entry:
        return []
    entry_html = fetch_url(source.entry)
    direct = discover_xlsx_links(source.entry, entry_html)
    if direct:
        return direct

    text = entry_html.decode("utf-8", errors="ignore")
    notice_urls: List[str] = []
    for match in _HREF_RE.finditer(text):
        href, anchor = match.group(1), _TAG_RE.sub("", match.group(2))
        if not _ANNOUNCEMENT_RE.search(anchor):
            continue
        url = urljoin(source.entry, href.split("#", 1)[0])
        if url.startswith("http") and url != source.entry and url not in notice_urls:
            notice_urls.append(url)
        if len(notice_urls) >= notice_limit:
            break

    links: List[str] = []
    for url in notice_urls:
        try:
            for link in discover_xlsx_links(url, fetch_url(url)):
                if link not in links:
                    links.append(link)
        except Exception:
            continue
    return links


def download_attachment(
    url: str, dest_dir: Path, source_id: str, referer: str = ""
) -> Optional[Path]:
    """下载附件到 dest_dir，命名 <source_id>__<时间戳>.<ext>；失败返回 None。"""
    try:
        data = fetch_url(url)
    except Exception:
        raise
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = ".xlsx" if url.lower().split("?")[0].endswith((".xlsx", ".xls")) else ".bin"
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"{source_id}__{ts}{ext}"
    dest.write_bytes(data)
    return dest


def harvest_source(
    source, inbox: Path
) -> Tuple[bool, str, List[Path]]:
    """对一个源站执行：抓入口页 → 找 xlsx → 下载新附件。
    返回 (ok, detail, downloaded_files)。任何失败都降级为 (False, 原因, [])。
    """
    if not source.entry:
        return False, "未配置入口 URL", []
    try:
        links = discover_source_xlsx_links(source)
    except Exception as e:
        return False, f"入口页抓取失败: {type(e).__name__}: {e}", []
    files: List[Path] = []
    for link in links[:5]:  # 单源单次最多 5 个附件，防爆量
        try:
            f = download_attachment(link, inbox, source.id)
            if f:
                files.append(f)
        except Exception:
            continue
    detail = f"发现 {len(links)} 个附件链接，下载 {len(files)} 个"
    return True, detail, files
