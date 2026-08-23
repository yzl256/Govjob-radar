# 每日匹配管道：岗位批 → 用户档案 → 三态分组 → 日报对象
from __future__ import annotations

from datetime import date
from typing import Dict, List, NamedTuple, Optional

from pydantic import BaseModel

from app.matching.engine import MatchResult, Matcher, Verdict
from app.models.job import Job
from app.models.profile import UserProfile


class JobWithResult(BaseModel):
    job: Job
    result: MatchResult

    class Config:
        arbitrary_types_allowed = True


class DailyReport(BaseModel):
    profile_name: str
    today: date
    eligible: List[JobWithResult] = []
    insufficient: List[JobWithResult] = []
    ineligible_count: int = 0
    top_fail_reasons: List[str] = []  # 不可报主因统计（前3）

    class Config:
        arbitrary_types_allowed = True

    def render_text(self) -> str:
        lines: List[str] = []
        name = self.profile_name or "你"
        lines.append(f"【今日可报岗位日报】{self.today.isoformat()}")
        lines.append(f"可报 {len(self.eligible)} 条 | 信息不足 {len(self.insufficient)} 条 | 不可报 {self.ineligible_count} 条")
        lines.append("")
        if self.eligible:
            lines.append(f"✅ 可报（{len(self.eligible)}）")
            for jr in self.eligible[:10]:
                quota = f"〔招{jr.job.quota}人〕" if jr.job.quota else ""
                lines.append(
                    f"  {jr.job.title} · {jr.job.employer}{quota} · {jr.job.region_detail}"
                )
                if jr.job.apply_deadline:
                    lines.append(f"    截止 {jr.job.apply_deadline.isoformat()}  {jr.job.source_url}")
            if len(self.eligible) > 10:
                lines.append(f"  …另有 {len(self.eligible) - 10} 条，见 H5")
        else:
            lines.append("✅ 可报（0）今天暂无完全符合条件的岗位")
        lines.append("")
        if self.insufficient:
            lines.append(f"⚠️ 信息不足（{len(self.insufficient)}）——补全档案可能解锁")
            for jr in self.insufficient[:5]:
                unknowns = "；".join(r.detail for r in jr.result.reasons if r.status == "unknown")
                lines.append(f"  {jr.job.title} · {jr.job.employer}")
                lines.append(f"    缺：{unknowns}")
        if self.top_fail_reasons:
            lines.append("")
            lines.append("❌ 不可报主因：" + "；".join(self.top_fail_reasons))
        return "\n".join(lines)


def split_by_deadline(jobs: List[Job], today: Optional[date] = None) -> tuple:
    """按截止日二分：(有效期内, 已截止)——「可报名投递」与「备考存档」两个桶。
    无截止日归入有效期内（无法判定不误删）。"""
    today = today or date.today()
    active: List[Job] = []
    expired: List[Job] = []
    for j in jobs:
        if j.apply_deadline is not None and j.apply_deadline < today:
            expired.append(j)
        else:
            active.append(j)
    return active, expired


def filter_active_jobs(jobs: List[Job], today: Optional[date] = None) -> List[Job]:
    """只保留有效期内的岗位：截止日 ≥ 今日（当天截止仍可报）；无截止日保留（无法判定不误删）。"""
    return split_by_deadline(jobs, today)[0]


def build_report(
    profile: UserProfile,
    jobs: List[Job],
    matcher: Matcher,
    today: Optional[date] = None,
) -> DailyReport:
    today = today or date.today()
    report = DailyReport(profile_name=profile.name, today=today)
    fail_fields: Dict[str, int] = {}

    for job in jobs:
        res = matcher.match(job, profile, today=today)
        if res.verdict == Verdict.ELIGIBLE:
            report.eligible.append(JobWithResult(job=job, result=res))
        elif res.verdict == Verdict.INSUFFICIENT:
            report.insufficient.append(JobWithResult(job=job, result=res))
        else:
            report.ineligible_count += 1
            for r in res.reasons:
                if r.status == "fail":
                    fail_fields[r.field] = fail_fields.get(r.field, 0) + 1
                    break  # 只记主因（第一个 fail）

    report.top_fail_reasons = [
        f"{k}×{v}" for k, v in sorted(fail_fields.items(), key=lambda x: -x[1])[:3]
    ]
    return report
