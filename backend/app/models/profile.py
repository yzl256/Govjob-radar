# 用户档案模型
# 设计要点见《体制内岗位推荐系统-设计文档》§3：
#  - 存出生日期而非年龄（岗位写法是"1998年7月以后出生"）
#  - 支持多段学历（本科+硕士各自带专业代码，代码按学历层次绑定对应目录）
#  - 匹配所需关键字段允许 None —— None 产出 "⚠️信息不足" 而非误判
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class EduLevel(str, Enum):
    COLLEGE = "大专"
    BACHELOR = "本科"
    MASTER = "硕士"
    DOCTOR = "博士"


LEVEL_ORDER = {
    EduLevel.COLLEGE: 1,
    EduLevel.BACHELOR: 2,
    EduLevel.MASTER: 3,
    EduLevel.DOCTOR: 4,
}


class PoliticalStatus(str, Enum):
    PARTY = "党员"
    CANDIDATE = "预备党员"
    LEAGUE = "团员"
    MASSES = "群众"


class FreshStatus(str, Enum):
    FRESH = "应届毕业生"
    CALM_PERIOD = "择业期内未落实工作"  # 多数公告视同应届，口径以原文为准
    NOT_FRESH = "往届"


class EducationRecord(BaseModel):
    """一段学历。catalog 由知识库推断（本科→undergraduate；硕士/博士按代码判断
    academic / professional），也可显式指定。"""

    level: EduLevel
    major_name: str = ""
    major_code: str = ""  # 本科6位(可带K/T后缀)；研究生4位
    catalog: Optional[str] = None  # undergraduate / academic / professional
    school: str = ""
    is_double_first_class: Optional[bool] = None  # 双一流（None=未知）
    graduation_date: Optional[date] = None


class UserProfile(BaseModel):
    name: str = ""
    birth_date: Optional[date] = None  # 岗位写法"1998年7月以后出生"靠出生日期判定；未填→⚠️
    gender: Optional[str] = None  # 男/女；未填且岗位限性别→⚠️
    political_status: Optional[PoliticalStatus] = None
    fresh_status: Optional[FreshStatus] = None
    is_student_cadre: Optional[bool] = None
    has_school_award: Optional[bool] = None
    household_province: Optional[str] = None  # 户籍省
    origin_province: Optional[str] = None  # 生源省
    subscribed_provinces: List[str] = []  # 订阅省份（驱动 C 类源站按需启用）
    career_interests: List[str] = []  # 仅扩展国企央企领域推荐，不参与体制内硬匹配
    education: List[EducationRecord] = []

    @property
    def highest_level(self) -> Optional[EduLevel]:
        if not self.education:
            return None
        return max(self.education, key=lambda r: LEVEL_ORDER[r.level]).level

    def records_in_band(self, lo: Optional[EduLevel], hi: Optional[EduLevel]) -> List[EducationRecord]:
        """学历层次落在 [lo, hi] 区间内的记录（含边界）。"""
        out = []
        for r in self.education:
            v = LEVEL_ORDER[r.level]
            if lo is not None and v < LEVEL_ORDER[lo]:
                continue
            if hi is not None and v > LEVEL_ORDER[hi]:
                continue
            out.append(r)
        return out
