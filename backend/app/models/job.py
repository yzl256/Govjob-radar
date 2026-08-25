# 岗位统一数据模型（与 DB jobs 表对应的内存对象）
# 资格约束全部结构化，匹配引擎不读公告原文。
from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, field_validator

from app.models.profile import EduLevel, PoliticalStatus


class MajorRuleType(str, Enum):
    ANY = "any"  # 专业不限
    CODE = "code"  # 精确专业代码（OR 列表）
    PREFIX = "prefix"  # 类(4位)/门类(2位)前缀
    TEXT = "text"  # 模糊写法，经别名表解析


class MajorRule(BaseModel):
    type: MajorRuleType
    value: Optional[Union[str, List[str]]] = None
    # 可选目录限定：undergraduate/academic/professional。
    # 不填时：code 按位数精确匹配；prefix 跨目录前缀匹配（0809 在本科=计算机类、
    # 在学术=电子科学与技术，均可能命中——需要精确口径的岗位务必填 scope）。
    scope: Optional[str] = None

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in {"undergraduate", "academic", "professional"}:
            raise ValueError(f"未知 scope: {v}")
        return v


class EduRequire(BaseModel):
    min_level: Optional[EduLevel] = None  # 本科及以上
    max_level: Optional[EduLevel] = None  # 硕士及以下
    exact_level: Optional[EduLevel] = None  # 仅限本科（高于或低于均不可报）

    def band(self) -> tuple[Optional[EduLevel], Optional[EduLevel]]:
        if self.exact_level is not None:
            return self.exact_level, self.exact_level
        return self.min_level, self.max_level


class MajorPolicy(str, Enum):
    """以哪段学历的专业报考。口径随招考类型不同："""

    HIGHEST_ONLY = "highest_only"  # 仅最高学历专业可报（国考/多数省考口径）
    ANY_DEGREE = "any_degree"  # 任一符合层次要求的学历专业均可（事业编/人才引进常见口径）


# 路径默认口径（seed，可被岗位级覆盖）
PATH_MAJOR_POLICY_DEFAULT = {
    1: MajorPolicy.HIGHEST_ONLY,  # 选调生
    2: MajorPolicy.HIGHEST_ONLY,  # 国考
    3: MajorPolicy.HIGHEST_ONLY,  # 省考
    4: MajorPolicy.ANY_DEGREE,  # 事业单位
    5: MajorPolicy.ANY_DEGREE,  # 人才引进
    6: MajorPolicy.ANY_DEGREE,  # 国企央企
    7: MajorPolicy.HIGHEST_ONLY,  # 军队文职
    8: MajorPolicy.ANY_DEGREE,  # 三支一扶
    9: MajorPolicy.ANY_DEGREE,  # 特岗/西部计划
    10: MajorPolicy.ANY_DEGREE,  # 辅导员/社区
}


class Job(BaseModel):
    id: str = ""
    path: int  # 1..10，见设计文档 §2
    title: str
    employer: str = ""
    region_province: str = ""
    region_detail: str = ""

    edu_require: EduRequire = EduRequire()
    major_rules: List[MajorRule] = []  # 空 = 专业不限
    major_policy: Optional[MajorPolicy] = None  # None → 取路径默认

    birth_after: Optional[date] = None  # "1998年7月以后出生" → 1998-07-01
    age_max: Optional[int] = None  # 截止日报考最大周岁
    gender_limit: Optional[str] = None  # None=不限 / "男" / "女"
    political_req: Optional[List[PoliticalStatus]] = None  # None=不限
    fresh_only: bool = False
    household_provinces: Optional[List[str]] = None  # None=不限；户籍或生源任一命中即可
    require_double_first_class: bool = False
    require_student_cadre: bool = False
    require_award: bool = False

    apply_deadline: Optional[date] = None
    quota: Optional[int] = None
    exam_type: Optional[int] = None  # 1笔试 2免笔试直接面试
    highlights: str = ""
    responsibilities: str = ""  # 国企职责/培养方向，必须来自公告原文或忠实摘要
    compensation: str = ""  # 待遇；未披露保持空
    application_url: str = ""  # 网申/报名入口
    application_process: str = ""  # 笔试/面试/资格审查等公告原文
    verification_status: str = "pending"  # verified=已有岗位表或完整条件；pending=公告待核验
    verification_note: str = ""
    other_notes: str = ""  # 未结构化的其他条件，透出给用户"以原文为准"
    source_url: str = ""


_BIRTH_RE = re.compile(r"(19\d{2}|20\d{2})\s*年\s*(\d{1,2})\s*月")


def parse_birth_after(text: str) -> Optional[date]:
    """'1998年7月以后出生' / '1998年7月1日及以后出生' → date(1998,7,1)"""
    if not text:
        return None
    m = _BIRTH_RE.search(text)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), 1)
