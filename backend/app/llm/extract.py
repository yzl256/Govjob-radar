# C 类公告 LLM 抽取：公告文本 → Job 候选 JSON → 校验归一化为 Job 模型。
# 设计原则（宁缺勿滥）：
#   - LLM 只做"从文本抄写"，专业要求抄原文片段，代码语义解析全部在本地知识层
#     （parse_major_cell + 三套目录 + 别名表）——LLM 不猜代码。
#   - 找不到的字段一律 null → 匹配引擎产出 ⚠️ 而非误判。
#   - 每个岗位带 evidence 引文，供人工核对。
from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import List, Optional

from app.knowledge.catalogs import Catalog
from app.knowledge.major_parse import parse_major_cell
from app.models.job import EduRequire, Job, MajorPolicy, MajorRule
from app.models.profile import EduLevel, PoliticalStatus

SYSTEM_PROMPT = """你是体制内招聘公告结构化抽取器。只依据用户提供的公告文本抽取，禁止编造；文本中找不到的字段一律填 null。

输出纯 JSON 对象（不要 markdown 代码块），schema：
{
  "is_job_announcement": true,
  "jobs": [
    {
      "title": "岗位/计划名称",
      "employer": "招聘单位（市级计划填牵头单位）",
      "region": "省市，如 广东省深圳市",
      "quota": 10,
      "edu_min": "本科",
      "majors": ["计算机类", "0854 电子信息", "不限"],
      "birth_after": "1998-07-01",
      "age_max": 35,
      "gender_limit": null,
      "political_req": "中共党员（含预备党员）",
      "fresh_only": false,
      "household_provinces": ["广东省"],
      "require_double_first_class": false,
      "apply_deadline": "2026-09-10",
      "publish_date": "2026-08-20",
      "source_hint": "文本来源说明（可 null）",
      "evidence": {"title": "≤40字原文引句", "edu": "≤40字原文引句", "major": "≤40字原文引句", "deadline": "≤40字原文引句"}
    }
  ]
}

字段口径：
- is_job_announcement：页面不是招聘/招录公告时 false 且 jobs 为空数组。
- edu_min：公告明确的最低学历层次，只能取 大专/本科/硕士/博士 之一；未提及填 null。
- majors：把专业要求原文片段逐条照抄进数组（保留原有代码和名称，不要自行换算代码）；
  "不限"/"专业不限"/"专业方向不限" 时输出 ["不限"]；未提及专业要求输出 []。
- 日期一律 YYYY-MM-DD；原文只有"2026年9月10日"这类中文日期时自行转换；无法定位确切日期填 null。
- birth_after：出生日期下限（如"1998年7月以后出生"→1998-07-01）。
- gender_limit：只允许 "男"/"女"/null。
- political_req：照抄原文表述；无要求填 null。
- household_provinces：户籍/生源限制省份（"广东省"全称），无限制输出 []。
- 一个公告含多个岗位且要求不同就输出多个 job；要求相同的一批岗位合并为一个（title 写明批次）。
- evidence 里每个关键字段给 ≤40 字原文引句，找不到就不给该键。"""


def _parse_date_flex(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


_LEVEL_MAP = {"大专": EduLevel.COLLEGE, "本科": EduLevel.BACHELOR,
              "硕士": EduLevel.MASTER, "研究生": EduLevel.MASTER, "博士": EduLevel.DOCTOR}


def _norm_level(v) -> Optional[EduLevel]:
    if not v:
        return None
    return _LEVEL_MAP.get(str(v).strip())


def _norm_political(v) -> Optional[List[PoliticalStatus]]:
    """'中共党员（含预备党员）' → [党员, 预备党员]；'团员' → [团员]；其余 → None(不限)。"""
    if not v:
        return None
    s = str(v)
    out: List[PoliticalStatus] = []
    if "党员" in s:
        out.append(PoliticalStatus.PARTY)
        if "预备" in s or "含预备" in s:
            out.append(PoliticalStatus.CANDIDATE)
    elif "团员" in s:
        out.append(PoliticalStatus.LEAGUE)
    elif "群众" in s:
        out.append(PoliticalStatus.MASSES)
    return out or None


def _norm_majors(raw_list, catalogs) -> List[MajorRule]:
    """LLM 抄写的专业原文片段 → MajorRule（复用确定性解析器，一片段可产多条规则）。"""
    rules: List[MajorRule] = []
    for fragment in raw_list or []:
        if not isinstance(fragment, str) or not fragment.strip():
            continue
        for rule in parse_major_cell(fragment, catalogs) or []:
            if not any(
                r.type == rule.type and r.value == rule.value and r.scope == rule.scope
                for r in rules
            ):
                rules.append(rule)
    return rules


def _stable_id(source_id: str, url: str, title: str, employer: str) -> str:
    basis = url or f"{title}|{employer}"
    h = hashlib.sha1(f"{source_id}|{basis}".encode()).hexdigest()[:8]
    return f"{source_id}-{h}"


def normalize_llm_jobs(
    data: dict,
    source_id: str,
    path: int,
    catalogs,
    source_url: str = "",
    url: str = "",
) -> List[Job]:
    """LLM JSON → List[Job]。单条岗位字段异常时丢弃该条并继续，不拖垮整批。"""
    if not data.get("is_job_announcement", True):
        return []
    jobs: List[Job] = []
    for raw in data.get("jobs") or []:
        try:
            jobs.append(_build_one(raw, source_id, path, catalogs, source_url or url))
        except Exception:
            continue
    return jobs


def _build_one(raw: dict, source_id: str, path: int, catalogs, url: str) -> Job:
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("缺 title")
    employer = str(raw.get("employer") or "").strip()
    region = str(raw.get("region") or "").strip()
    edu_min = _norm_level(raw.get("edu_min"))

    evid = raw.get("evidence") or {}
    evid_txt = "；".join(f"{k}「{v}」" for k, v in evid.items() if v) if isinstance(evid, dict) else ""

    return Job(
        id=_stable_id(source_id, raw.get("source_url") or url, title, employer),
        path=int(path),
        title=title,
        employer=employer,
        region_province=region[:3] if region else "",
        region_detail=region or employer[:12],
        edu_require=EduRequire(min_level=edu_min),
        major_rules=_norm_majors(raw.get("majors"), catalogs),
        major_policy=None,  # 用路径默认口径；岗位级覆盖等真实案例出现再加
        birth_after=_parse_date_flex(raw.get("birth_after")),
        age_max=raw.get("age_max") if isinstance(raw.get("age_max"), int) else None,
        gender_limit=(str(raw.get("gender_limit")) if raw.get("gender_limit") in ("男", "女") else None),
        political_req=_norm_political(raw.get("political_req")),
        fresh_only=bool(raw.get("fresh_only")),
        household_provinces=[str(x) for x in (raw.get("household_provinces") or [])] or None,
        require_double_first_class=bool(raw.get("require_double_first_class")),
        apply_deadline=_parse_date_flex(raw.get("apply_deadline")),
        quota=raw.get("quota") if isinstance(raw.get("quota"), int) else None,
        highlights="",
        other_notes=(f"LLM 抽取自公告。证据：{evid_txt}" if evid_txt else "LLM 抽取自公告"),
        source_url=raw.get("source_url") or url,
    )


def extract_jobs_from_text(
    text: str,
    llm,
    source_id: str,
    path: int,
    catalogs,
    source_url: str = "",
) -> List[Job]:
    """一段公告文本 → List[Job]（调用 LLM + 归一化）。"""
    user_prompt = f"公告文本（来源 {source_id}）：\n{text}"
    data = llm.chat_json(SYSTEM_PROMPT, user_prompt)
    return normalize_llm_jobs(data, source_id, path, catalogs, source_url=source_url)
