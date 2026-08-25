"""岗位库维护编排：本地归档优先，来源页复核作为低频补充。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.pipeline.source_review import invalid_source_urls, review_stored_sources
from app.store.jobs import ArchiveSummary, archive_invalid_jobs


@dataclass(frozen=True)
class MaintenanceSummary:
    archive: ArchiveSummary
    reviewed_sources: int = 0
    discovered_result_sources: int = 0
    source_review_failures: int = 0


def maintain_job_store(
    root: Path,
    today: date | None = None,
    review_sources: bool = False,
    review_limit: int = 3,
) -> MaintenanceSummary:
    """执行一轮可恢复维护。

    先用本地已有的“结果公示来源”标记归档；需要时再低频复核少量来源页，
    一旦发现新结果公示立即进行第二次归档。网页请求只使用前一段本地操作，
    不会为了匹配结果等待外网。
    """
    root = Path(root)
    archive = archive_invalid_jobs(
        root, today=today, invalid_source_urls=invalid_source_urls(root)
    )
    if not review_sources:
        return MaintenanceSummary(archive=archive)

    review = review_stored_sources(root, max_sources=review_limit)
    if review.result_publications:
        archive = archive.merged(
            archive_invalid_jobs(
                root, today=today, invalid_source_urls=invalid_source_urls(root)
            )
        )
    return MaintenanceSummary(
        archive=archive,
        reviewed_sources=review.reviewed,
        discovered_result_sources=review.result_publications,
        source_review_failures=review.failed,
    )
