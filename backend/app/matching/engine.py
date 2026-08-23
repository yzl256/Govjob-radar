# 匹配引擎：硬过滤 → 三态判定 + 逐条可解释原因
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel

from app.knowledge.alias import AliasTable
from app.knowledge.catalogs import Catalog
from app.matching.majors import build_candidates, eval_major_rules
from app.models.job import Job, MajorPolicy, PATH_MAJOR_POLICY_DEFAULT
from app.models.profile import (
    LEVEL_ORDER,
    EduLevel,
    PoliticalStatus,
    UserProfile,
)


class Verdict(str, Enum):
    ELIGIBLE = "可报"
    INSUFFICIENT = "信息不足"
    INELIGIBLE = "不可报"


class Reason(BaseModel):
    field: str
    status: str  # pass / fail / unknown
    detail: str


class MatchResult(BaseModel):
    verdict: Verdict
    reasons: List[Reason] = []
    attention: List[str] = []  # 透出给用户的人工阅读提示


class Matcher:
    def __init__(
        self,
        catalogs: Dict[str, Catalog],
        aliases: AliasTable,
        schools=None,  # Optional[SchoolTable]；None 时懒加载官方双一流名单
    ):
        self.catalogs = catalogs
        self.aliases = aliases
        self._schools = schools

    @property
    def schools(self):
        if self._schools is None:
            from app.knowledge.schools import load_schools

            self._schools = load_schools()
        return self._schools

    def match(self, job: Job, profile: UserProfile, today: Optional[date] = None) -> MatchResult:
        reasons: List[Reason] = []
        attention: List[str] = []
        today = today or date.today()

        if job.apply_deadline and job.apply_deadline < today:
            attention.append(
                f"该公告报名已于 {job.apply_deadline.isoformat()} 截止，岗位仅作存档展示"
            )

        reasons.append(self._gender(job, profile))
        reasons.append(self._age(job, profile, today))
        reasons.append(self._edu_level(job, profile))
        reasons.append(self._major(job, profile))
        if job.fresh_only:
            reasons.append(self._fresh(job, profile, attention))
        if job.political_req:
            reasons.append(self._political(job, profile))
        if job.household_provinces:
            reasons.append(self._household(job, profile))
        if job.require_double_first_class:
            reasons.append(self._dfc(profile))
        if job.require_student_cadre:
            reasons.append(self._flag("学生干部经历", profile.is_student_cadre))
        if job.require_award:
            reasons.append(self._flag("校级以上表彰", profile.has_school_award))
        if job.other_notes:
            attention.append(f"其他报名条件（以原文为准）：{job.other_notes}")

        if any(r.status == "fail" for r in reasons):
            verdict = Verdict.INELIGIBLE
        elif any(r.status == "unknown" for r in reasons):
            verdict = Verdict.INSUFFICIENT
        else:
            verdict = Verdict.ELIGIBLE
        return MatchResult(verdict=verdict, reasons=reasons, attention=attention)

    # ── 单项检查 ────────────────────────────────────────────
    def _gender(self, job: Job, p: UserProfile) -> Reason:
        if not job.gender_limit:
            return Reason(field="性别", status="pass", detail="不限")
        if p.gender is None:
            return Reason(field="性别", status="unknown", detail=f"限{job.gender_limit}，档案未填性别")
        ok = p.gender == job.gender_limit
        return Reason(
            field="性别",
            status="pass" if ok else "fail",
            detail=f"限{job.gender_limit}，你是{p.gender}",
        )

    def _age(self, job: Job, p: UserProfile, today: date) -> Reason:
        if job.birth_after is None and job.age_max is None:
            return Reason(field="年龄", status="pass", detail="不限")
        if p.birth_date is None:
            return Reason(field="年龄", status="unknown", detail="岗位有年龄/出生日期要求，档案未填出生日期")
        if job.birth_after is not None:
            ok = p.birth_date >= job.birth_after
            fmt = job.birth_after.strftime("%Y年%m月%d日")
            if not ok:
                return Reason(
                    field="年龄",
                    status="fail",
                    detail=f"要求{fmt}以后出生，你的出生日期{p.birth_date.isoformat()}",
                )
        if job.age_max is not None:
            ref = job.apply_deadline or today
            age = ref.year - p.birth_date.year - (
                (ref.month, ref.day) < (p.birth_date.month, p.birth_date.day)
            )
            if age > job.age_max:
                return Reason(
                    field="年龄",
                    status="fail",
                    detail=f"截止日报考年龄需≤{job.age_max}周岁，你届时{age}周岁",
                )
        return Reason(field="年龄", status="pass", detail="符合")

    def _edu_level(self, job: Job, p: UserProfile) -> Reason:
        highest = p.highest_level
        if highest is None:
            return Reason(field="学历", status="unknown", detail="未填写学历")
        req = job.edu_require
        if req.exact_level is not None:
            ok = highest == req.exact_level
            return Reason(
                field="学历",
                status="pass" if ok else "fail",
                detail=f"仅限{req.exact_level.value}，你的最高学历{highest.value}",
            )
        if req.min_level is not None and LEVEL_ORDER[highest] < LEVEL_ORDER[req.min_level]:
            return Reason(
                field="学历",
                status="fail",
                detail=f"要求{req.min_level.value}及以上，你的最高学历{highest.value}",
            )
        if req.max_level is not None and LEVEL_ORDER[highest] > LEVEL_ORDER[req.max_level]:
            return Reason(
                field="学历",
                status="fail",
                detail=f"要求{req.max_level.value}及以下，你的最高学历{highest.value}",
            )
        return Reason(field="学历", status="pass", detail=f"最高学历{highest.value}，符合")

    def _major(self, job: Job, p: UserProfile) -> Reason:
        lo, hi = job.edu_require.band()
        policy = job.major_policy or PATH_MAJOR_POLICY_DEFAULT.get(job.path, MajorPolicy.HIGHEST_ONLY)
        candidates, missing = build_candidates(p, lo, hi, policy, self.catalogs)
        rules = job.major_rules
        if not rules:
            return Reason(field="专业", status="pass", detail="专业不限")
        verdict, detail = eval_major_rules(rules, candidates, self.aliases)
        if missing and verdict is not True:
            detail = f"{detail}（{missing}）"
        status = {True: "pass", False: "fail", None: "unknown"}[verdict]
        return Reason(field="专业", status=status, detail=f"[{policy.value}] {detail}")

    def _fresh(self, job: Job, p: UserProfile, attention: List[str]) -> Reason:
        if p.fresh_status is None:
            return Reason(field="应届", status="unknown", detail="限应届，档案未填毕业状态")
        if p.fresh_status.value == "往届":
            return Reason(field="应届", status="fail", detail="限应届毕业生，你是往届")
        if p.fresh_status.value == "择业期内未落实工作":
            attention.append("择业期视同应届的口径以招考公告原文为准")
        return Reason(field="应届", status="pass", detail=f"{p.fresh_status.value}（限应届岗位）")

    def _political(self, job: Job, p: UserProfile) -> Reason:
        if p.political_status is None:
            return Reason(field="政治面貌", status="unknown", detail="岗位有要求，档案未填")
        req = set(job.political_req)
        user = p.political_status
        ok = user in req or (user == PoliticalStatus.CANDIDATE and PoliticalStatus.PARTY in req)
        req_txt = "或".join(s.value for s in job.political_req)
        return Reason(
            field="政治面貌",
            status="pass" if ok else "fail",
            detail=f"要求{req_txt}，你是{user.value}",
        )

    @staticmethod
    def _norm_prov(x: Optional[str]) -> str:
        """省份名归一化：去掉 省/市/壮族自治区 等后缀，'广东省'=='广东'。"""
        if not x:
            return ""
        s = x.strip()
        for suf in ("维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市"):
            if s.endswith(suf) and len(s) > len(suf):
                return s[: -len(suf)]
        return s

    def _household(self, job: Job, p: UserProfile) -> Reason:
        if p.household_province is None and p.origin_province is None:
            return Reason(field="户籍/生源", status="unknown", detail="岗位限户籍/生源，档案未填")
        allowed = {self._norm_prov(x) for x in job.household_provinces}
        mine = {self._norm_prov(x) for x in (p.household_province, p.origin_province) if x}
        ok = bool(allowed & mine)
        return Reason(
            field="户籍/生源",
            status="pass" if ok else "fail",
            detail=f"限{'/'.join(sorted(allowed))}，你户籍{p.household_province}、生源{p.origin_province}",
        )

    def _dfc(self, p: UserProfile) -> Reason:
        recs = sorted(p.education, key=lambda r: -LEVEL_ORDER[r.level])
        if not recs:
            return Reason(field="双一流", status="unknown", detail="岗位限双一流高校，档案未填学历")
        # 1) 显式声明优先（用户明确填过的记录）
        declared = {r.is_double_first_class for r in recs}
        if True in declared:  # 任一学历院校是双一流即视为满足
            top = next(r for r in recs if r.is_double_first_class)
            return Reason(field="双一流", status="pass", detail=f"限双一流，{top.school or '你的院校'}为双一流")
        # 2) 校名知识表推断：命中官方 147 所名单
        for r in recs:
            if r.school:
                hit = self.schools.lookup(r.school)
                if hit:
                    return Reason(
                        field="双一流",
                        status="pass",
                        detail=f"限双一流，{r.school}（{hit}）在官方双一流名单内",
                    )
        # 3) 全部显式否认 → 不可报；否则信息不足
        if declared == {False}:
            return Reason(field="双一流", status="fail", detail="限双一流，你填写的高校均不是双一流")
        named = "、".join(filter(None, (r.school for r in recs)))
        if named:
            return Reason(
                field="双一流",
                status="unknown",
                detail=f"限双一流，院校（{named}）未在名单内匹配到，请核对校名全称；独立学院不计入",
            )
        return Reason(field="双一流", status="unknown", detail="岗位限双一流高校，档案未填毕业院校")

    def _flag(self, name: str, val: Optional[bool]) -> Reason:
        if val is None:
            return Reason(field=name, status="unknown", detail=f"岗位要求{name}，档案未填")
        return Reason(
            field=name,
            status="pass" if val else "fail",
            detail=f"要求{name}，你{'有' if val else '没有'}",
        )
