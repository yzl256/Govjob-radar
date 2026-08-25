"""公告来源复核：发现“职位表其实来自结果公示”的历史脏数据。"""
from __future__ import annotations

import json
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from app.crawler.fetch import fetch_url
from app.llm.htmltext import html_to_text
from app.store.jobs import load_jobs
from app.validity import is_result_publication_page


def review_file(root: Path) -> Path:
    return Path(root) / "data" / "source_reviews.json"


def _load_reviews(root: Path) -> Dict[str, dict]:
    path = review_file(root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sources = raw.get("sources", raw) if isinstance(raw, dict) else {}
    return {str(url): row for url, row in sources.items() if isinstance(row, dict)}


def _save_reviews(root: Path, reviews: Dict[str, dict]) -> None:
    path = review_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": reviews,
    }
    # 先落同目录临时文件再替换，避免服务意外停止时留下半截 JSON。
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as fh:
        tmp = Path(fh.name)
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)


def record_source_review(root: Path, url: str, status: str, detail: str = "") -> None:
    """记录一次来源页复核；供抓取器和定时复核共用。"""
    if not url.startswith(("http://", "https://")):
        return
    reviews = _load_reviews(root)
    reviews[url] = {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "detail": (detail or "")[:240],
    }
    _save_reviews(root, reviews)


def invalid_source_urls(root: Path) -> Set[str]:
    """返回已经人工/程序核验为结果公示的来源 URL。"""
    return {
        url for url, row in _load_reviews(root).items()
        if row.get("status") == "result_publication"
    }


@dataclass(frozen=True)
class SourceReviewSummary:
    reviewed: int = 0
    result_publications: int = 0
    failed: int = 0
    result_urls: tuple[str, ...] = ()


def _reviewed_today(row: Optional[dict], now: datetime) -> bool:
    """正常或失败都最多每日重试一次，避免后台维护高频打扰源站。"""
    if not row:
        return False
    try:
        checked = datetime.fromisoformat(str(row.get("checked_at") or ""))
    except ValueError:
        return False
    return checked.date() == now.date()


def review_stored_sources(
    root: Path,
    max_sources: int = 3,
    fetcher: Optional[Callable[[str], bytes]] = None,
    now: Optional[datetime] = None,
) -> SourceReviewSummary:
    """复核当前岗位库中最影响展示的少量来源页。

    不凭“页面暂时打不开”删除岗位；只有页面文本明确是名单/录取结果公示时，
    才把 URL 标记为无效，后续归档器再可恢复地迁走这些岗位。
    """
    root = Path(root)
    now = now or datetime.now()
    reviews = _load_reviews(root)
    counts = Counter(
        j.source_url for j in load_jobs(root)
        if (j.source_url or "").startswith(("http://", "https://"))
    )
    candidates = [
        url for url, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if reviews.get(url, {}).get("status") != "result_publication"
        and not _reviewed_today(reviews.get(url), now)
    ][:max_sources]
    if not candidates:
        return SourceReviewSummary()

    fetch = fetcher or fetch_url
    result_urls: List[str] = []
    reviewed = failed = 0
    for url in candidates:
        try:
            raw = fetch(url)
            html = raw.decode("utf-8", errors="ignore")
            text = html_to_text(html)
            status = "result_publication" if is_result_publication_page(text, html=html) else "not_result"
            detail = text.splitlines()[0].strip() if text else "页面无可读正文"
            reviews[url] = {
                "status": status,
                "checked_at": now.isoformat(timespec="seconds"),
                "detail": detail[:240],
            }
            reviewed += 1
            if status == "result_publication":
                result_urls.append(url)
        except Exception as exc:
            reviews[url] = {
                "status": "fetch_failed",
                "checked_at": now.isoformat(timespec="seconds"),
                "detail": f"{type(exc).__name__}: {exc}"[:240],
            }
            failed += 1
    _save_reviews(root, reviews)
    return SourceReviewSummary(
        reviewed=reviewed,
        result_publications=len(result_urls),
        failed=failed,
        result_urls=tuple(result_urls),
    )
