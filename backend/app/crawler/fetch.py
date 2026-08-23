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
        html = fetch_url(source.entry)
    except Exception as e:
        return False, f"入口页抓取失败: {type(e).__name__}: {e}", []
    links = discover_xlsx_links(source.entry, html)
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
