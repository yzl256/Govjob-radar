"""国企央企：领域相关性推荐；体制内仍交给严格三态引擎。"""
from __future__ import annotations

from typing import Dict, List, Tuple

from app.models.job import Job
from app.models.profile import UserProfile

_STRONG = ("数据", "大数据", "人工智能", "ai", "算法", "软件", "开发", "后端", "前端", "信息化", "数字化", "网络安全", "云计算")
_EXPLORE = ("数据产品", "产品经理", "技术管培", "科技管培", "金融科技", "数字化转型", "技术运营")
_FUNCTIONAL = ("项目管理", "项目经理", "信息技术管理", "it管理", "数字化项目", "技术支持")


def recommend_soe(job: Job, profile: UserProfile) -> Tuple[str | None, str]:
    """返回 (strong/explore/functional/None, 可解释理由)。无证据的泛管培不推荐。"""
    major_text = " ".join(
        r.value if isinstance(r.value, str) else " ".join(r.value or []) for r in job.major_rules
    )
    text = " ".join((job.responsibilities, job.other_notes, job.title, major_text)).lower()
    interests = " ".join(profile.career_interests or []).lower()
    for label, words, reason in (("strong", _STRONG, "岗位职责或专业要求命中技术领域"), ("explore", _EXPLORE, "岗位培养方向与技术职业发展相关"), ("functional", _FUNCTIONAL, "属于技术关联的职能岗位")):
        hits = [w for w in words if w.lower() in text]
        extra = [x for x in (profile.career_interests or []) if x.lower() in text or x.lower() in interests and x.lower() in text]
        if hits or extra:
            return label, f"{reason}：{ '、'.join((hits + extra)[:3]) }"
    return None, "公告未提供与专业领域相关的职责、专业要求或培养方向"


def split_recommendations(jobs: List[Job], profile: UserProfile) -> Dict[str, List[tuple[Job, str]]]:
    out = {"strong": [], "explore": [], "functional": [], "pending": []}
    for job in jobs:
        if job.path != 6:
            continue
        level, reason = recommend_soe(job, profile)
        if level:
            out[level].append((job, reason))
        elif job.verification_status == "pending":
            out["pending"].append((job, reason))
    return out
