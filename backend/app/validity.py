"""岗位有效性判定：供抓取、归档和展示层共用同一套口径。"""
from __future__ import annotations

import html as html_lib
import re
from datetime import date
from typing import Iterable, List, Optional, Set, Tuple

from app.models.job import Job


# 结果公示会复用“招聘/聘用”等字样，不能只依赖招聘关键词判断。
_RESULT_PUBLICATION_RE = re.compile(
    r"派遣人员名单|拟录取|拟录用|拟聘用|录取人员名单|录用人员名单|聘用人员名单|"
    r"入围(?:人员)?名单|体检(?:人员)?名单|考察(?:人员)?名单|录取结果|录用结果|"
    r"(?:名单|录取|录用|聘用|拟聘).{0,16}公示"
)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def is_result_publication(text: str) -> bool:
    """是否为已经结束招聘的结果/名单公示，而非可报名公告。"""
    return bool(_RESULT_PUBLICATION_RE.search(text or ""))


def is_result_publication_page(text: str, html: str = "") -> bool:
    """按公告标题判定结果页，避免正文/页脚的其他新闻标题造成误判。"""
    if html:
        title = _TITLE_RE.search(html)
        if title:
            headline = html_lib.unescape(_TAG_RE.sub(" ", title.group(1))).strip()
            if headline:
                return is_result_publication(headline)
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    # html_to_text 会把 <title> 放在开头；极少数页面标题与面包屑分行时，保留
    # 前两行即可，不能扫描整页，否则“相关推荐/历史公告”会误杀正在招聘的页面。
    return is_result_publication(" ".join(lines[:2]))


def job_invalid_reason(
    job: Job,
    today: Optional[date] = None,
    invalid_source_urls: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """返回不可展示原因；``None`` 表示尚未发现确定性失效证据。

    无截止日期不等于失效：人才引进、国企长期招聘都可能没有统一截止日，
    因此这里绝不把它当作归档依据。来源页已核验为结果公示时，整份职位表
    的逐行岗位标题可能看不出“名单”二字，需按来源 URL 一并拦截。
    """
    invalid_urls: Set[str] = set(invalid_source_urls or [])
    if job.source_url and job.source_url in invalid_urls:
        return "result_publication"

    # 岗位标题与抽取备注是入库时最稳定的公告语义；不扫职责/专业字段，
    # 避免“需参加体检”等正常招聘流程被误判为结果公示。
    result_text = " ".join(
        x for x in (job.title, job.highlights, job.other_notes, job.source_url) if x
    )
    if is_result_publication(result_text):
        return "result_publication"

    today = today or date.today()
    if job.apply_deadline is not None and job.apply_deadline < today:
        return "deadline_passed"
    return None


def split_displayable_jobs(
    jobs: Iterable[Job],
    today: Optional[date] = None,
    invalid_source_urls: Optional[Iterable[str]] = None,
) -> Tuple[List[Job], List[Job], List[Job]]:
    """分为（可继续展示、已截止、结果/名单公示）。

    这是一条展示保护线：即使归档尚未来得及运行，失效记录也不会流向匹配、
    省份统计或前端卡片。
    """
    active: List[Job] = []
    expired: List[Job] = []
    result_publications: List[Job] = []
    today = today or date.today()
    invalid_urls = set(invalid_source_urls or [])
    for job in jobs:
        reason = job_invalid_reason(job, today=today, invalid_source_urls=invalid_urls)
        if reason == "deadline_passed":
            expired.append(job)
        elif reason == "result_publication":
            result_publications.append(job)
        else:
            active.append(job)
    return active, expired, result_publications
