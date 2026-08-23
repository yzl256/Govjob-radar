# 职位表解析器（国考 + 省考/选调/事业单位等省级职位表）
# 表头每年微调 → 列名别名映射；表头上方可能有标题行 → 按命中数自动定位表头行。
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.io.xlsx import read_workbook
from app.knowledge.major_parse import parse_major_cell
from app.models.job import EduRequire, Job, parse_birth_after
from app.models.profile import EduLevel, PoliticalStatus

# ── 列别名注册表（值命中任一别名即认为该列存在）──────────────────
COLUMN_ALIASES: Dict[str, List[str]] = {
    "dept": ["部门名称", "招考部门", "部门", "用人单位", "招聘单位", "招录单位", "主管部门", "招募单位", "选调单位"],
    "bureau": ["用人司局", "用人司局（单位）", "具体单位", "用人单位名称"],
    "title": ["招考职位", "职位名称", "职位", "招录职位", "岗位名称", "招聘岗位", "招募岗位", "选调职位"],
    "job_code": ["职位代码", "岗位代码", "招录职位代码"],
    "intro": ["职位简介", "岗位简介"],
    "quota": ["招考人数", "人数", "招录人数", "招聘人数", "计划人数", "需求人数", "拟聘人数", "招募人数", "选调人数"],
    "major": ["专业", "专业要求", "所需专业", "专业类别", "专业名称", "专业名称及代码", "专业（专业代码）", "专业(专业代码)"],
    "major_grad": ["专业要求(研究生)", "专业要求（研究生）", "研究生专业"],
    "major_ugrad": ["专业要求(本科)", "专业要求（本科）", "本科专业"],
    "edu": ["学历", "学历要求", "学历层次", "文化程度"],
    "degree": ["学位", "学位要求"],
    "political": ["政治面貌", "政治面貌要求", "是否要求党员"],
    "fresh_col": ["是否要求应届毕业生", "是否仅限应届"],
    "age": ["年龄", "年龄要求", "年龄条件"],
    "household": ["户籍要求", "户籍", "生源要求", "生源", "户籍所在地", "是否限制户籍"],
    "grassroots": ["基层工作最低年限"],
    "service": ["服务基层项目工作经历", "服务基层项目经历"],
    "workplace": ["工作地点", "工作地点（省市）", "工作地点(省 市)", "工作地点（省市区）", "工作地区", "用人单位所在地", "工作所在地", "招募地区"],
    "district": ["所属区县", "所属市（区）", "所属区"],
    "township": ["所属乡镇", "所属街道（乡镇）"],
    "cert": ["是否要求教师资格证", "是否需要医师资格证", "资格证要求"],
    "school_scope": ["选调高校范围", "招录高校范围", "高校范围"],
    "remark": ["备注", "其他条件", "其他要求"],
}

_FRESH_RE = re.compile(r"限应届|仅限应届|应届高校毕业生|应届毕业生")
_AGE_MAX_RE = re.compile(r"(\d{2})\s*周岁以下")

# 31 个省级行政区全称（职位表户籍/生源列识别用；直辖市以"市"结尾）
PROVINCES: Tuple[str, ...] = (
    "北京市", "天津市", "河北省", "山西省", "内蒙古自治区", "辽宁省", "吉林省",
    "黑龙江省", "上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省",
    "山东省", "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省",
    "重庆市", "四川省", "贵州省", "云南省", "西藏自治区", "陕西省", "甘肃省",
    "青海省", "宁夏回族自治区", "新疆维吾尔自治区",
)


def _parse_household_cell(text: str) -> Tuple[Optional[List[str]], str]:
    """户籍列 → (省份列表或 None, 未结构化残余文本)。枚举表包含匹配；其余透出。"""
    t = (text or "").replace(" ", "")
    if not t or "不限" in t or t in ("无", "--", "—", "面向全国"):
        return None, ""
    provs = [p for p in PROVINCES if p in t]
    if provs:
        return sorted(set(provs)), ""
    return None, t  # 如"限东莞市户籍"——市县级写法不硬解析，透出给用户


def _clean_header(h: str) -> str:
    return re.sub(r"\s+", "", h or "")


def locate_header(rows: List[List[str]]) -> Optional[int]:
    """返回表头行号（0 基）。判据：该行命中 ≥4 个已知列名。"""
    for i, row in enumerate(rows[:10]):
        hits = 0
        for cell in row:
            c = _clean_header(cell)
            if any(c == _clean_header(a) for aliases in COLUMN_ALIASES.values() for a in aliases):
                hits += 1
        if hits >= 4:
            return i
    return None


def _map_columns(header: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for idx, cell in enumerate(header):
        c = _clean_header(cell)
        for key, aliases in COLUMN_ALIASES.items():
            if key not in out and any(c == _clean_header(a) for a in aliases):
                out[key] = idx
    return out


def _cell(row: List[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def parse_edu_require(text: str) -> EduRequire:
    t = (text or "").replace(" ", "")
    if not t:
        return EduRequire()
    # 「仅限X」才是精确限定；裸写学历级别按国考官方口径=最低要求（高学历可报低岗）
    if "仅限本科" in t:
        return EduRequire(exact_level=EduLevel.BACHELOR)
    if "仅限硕士研究生" in t or "仅限硕士" == t:
        return EduRequire(exact_level=EduLevel.MASTER)
    if "仅限博士" in t:
        return EduRequire(exact_level=EduLevel.DOCTOR)
    if "大专或本科" in t:
        return EduRequire(min_level=EduLevel.COLLEGE, max_level=EduLevel.BACHELOR)
    if "本科或硕士研究生" in t or "本科或硕士" in t:
        return EduRequire(min_level=EduLevel.BACHELOR, max_level=EduLevel.MASTER)
    lo = hi = None
    if "博士" in t:
        lo = EduLevel.DOCTOR
    elif "硕士" in t or "研究生" in t:
        lo = EduLevel.MASTER
    elif "本科" in t:
        lo = EduLevel.BACHELOR
    elif "大专" in t or "专科" in t:
        lo = EduLevel.COLLEGE
    return EduRequire(min_level=lo, max_level=hi)


def parse_political(text: str) -> Optional[List[PoliticalStatus]]:
    t = (text or "").replace(" ", "")
    if not t or "不限" in t or t in ("否", "无", "--"):
        return None
    if t in ("是", "要求", "需"):  # 「是否要求党员」列的肯定值
        return [PoliticalStatus.PARTY]
    if "中共党员" in t and ("共青团员" in t or "团员" in t):
        return [PoliticalStatus.PARTY, PoliticalStatus.LEAGUE]
    if "中共党员" in t:
        return [PoliticalStatus.PARTY]
    if "共青团员" in t or "团员" in t:
        return [PoliticalStatus.LEAGUE]
    if "民主党派" in t or "群众" in t:
        return None  # 复杂口径透出到备注，不硬解析
    return None


def _pick_major_cell(edu_text: str, grad: str, ugrad: str, generic: str) -> str:
    """双专业列（研究生/本科分列）按该行学历取对应列；口径不明则两列并入。"""
    e = edu_text or ""
    if "研究生" in e or "硕士" in e or "博士" in e:
        return grad or generic
    if "本科" in e or "大专" in e or "专科" in e:
        return ugrad or generic
    if grad and ugrad:
        return f"{grad}、{ugrad}"
    return generic or grad or ugrad


def _norm_cell(text: str) -> str:
    return (text or "").strip()


def _other_notes(degree: str, grassroots: str, service: str, remark: str) -> str:
    parts = []
    if degree and "与学历相对应" not in degree and "无要求" not in degree:
        parts.append(f"学位要求：{degree}")
    if grassroots and grassroots not in ("无要求", "不限", "--"):
        parts.append(f"基层工作最低年限：{grassroots}")
    if service and service not in ("无要求", "不限", "--"):
        parts.append(f"服务基层项目经历：{service}")
    if remark:
        parts.append(f"备注：{remark}")
    return "；".join(parts)


def _parse_age_cell(text: str) -> Tuple[Optional[date], Optional[int]]:
    """年龄列 → (birth_after, age_max)。'35周岁以下'→(None,35)；
    '1998年7月以后出生'→(1998-07-01,None)；两者都有就都给。"""
    t = (text or "").replace(" ", "")
    if not t or "不限" in t or "无" == t:
        return None, None
    birth = parse_birth_after(t)
    m = _AGE_MAX_RE.search(t)
    age_max = int(m.group(1)) if m else None
    return birth, age_max


def parse_guokao_workbook(
    xlsx_path: str,
    catalogs,
    source_url: str = "",
    apply_deadline: Optional[date] = None,
    path: int = 2,
    job_prefix: str = "gk",
) -> List[Job]:
    """解析职位表 xlsx → List[Job]。多 sheet 逐个处理。
    path：十条路径编号（国考 2/省考 3/选调 1/事业 4…）；job_prefix：id 前缀（源站 id，
    防跨文件撞车）。"""
    jobs: List[Job] = []
    wb = read_workbook(xlsx_path)
    seq = 0
    for sheet_name, rows in wb.items():
        if not rows:
            continue
        h_idx = locate_header(rows)
        if h_idx is None:
            continue
        cols = _map_columns(rows[h_idx])
        for row in rows[h_idx + 1 :]:
            title = _cell(row, cols.get("title"))
            dept = _cell(row, cols.get("dept"))
            if not title or not (dept or _cell(row, cols.get("bureau"))):
                continue  # 跳过空行/统计行
            seq += 1
            remark = _cell(row, cols.get("remark"))
            intro = _cell(row, cols.get("intro"))
            fresh = bool(
                _FRESH_RE.search(remark)
                or _FRESH_RE.search(intro)
                or _norm_cell(_cell(row, cols.get("fresh_col"))).replace(" ", "")
                in ("是", "要求", "仅限应届")
            )
            birth, age_max = _parse_age_cell(_cell(row, cols.get("age")))

            # 户籍：省份枚举识别；「是否限制户籍=是」或「其他条件」里的市县户籍 → 透出备注
            hh_raw = _cell(row, cols.get("household"))
            household, hh_note = _parse_household_cell(hh_raw)
            if hh_note in ("是", "要求", "限"):
                hh_note = "限户籍（具体范围见公告原文）"
            if re.search(r"户籍|生源", remark):
                hh2, note2 = _parse_household_cell(remark)
                household = sorted(set(household or []) | set(hh2 or [])) or None
                if note2:
                    hh_note = (hh_note + "；" if hh_note else "") + note2

            # 地区：招募地区+所属区县+所属乡镇 拼成完整门第
            region_parts = [
                _norm_cell(_cell(row, cols.get(k))) for k in ("workplace", "district", "township")
            ]
            region_full = "".join(p for p in region_parts if p)

            notes = _other_notes(
                _cell(row, cols.get("degree")),
                _cell(row, cols.get("grassroots")),
                _cell(row, cols.get("service")),
                remark,
            )
            if hh_note:
                notes = (notes + "；" if notes else "") + f"户籍要求：{hh_note}"
            cert = _cell(row, cols.get("cert"))
            if cert and cert.strip() not in ("不限", "否", "无", ""):
                notes = (notes + "；" if notes else "") + f"资格证：{cert.strip()}"
            scope = _cell(row, cols.get("school_scope"))
            if scope:
                notes = (notes + "；" if notes else "") + f"高校范围：{scope.strip()}"

            # 专业：双列按学历取列（选调/省考表「专业要求(研究生)/(本科)」分列）
            major_text = _pick_major_cell(
                _cell(row, cols.get("edu")),
                _cell(row, cols.get("major_grad")),
                _cell(row, cols.get("major_ugrad")),
                _cell(row, cols.get("major")),
            )

            # id：岗位代码存在则内容寻址（重跑/改版不撞车），否则序号
            code = _cell(row, cols.get("job_code"))
            jid = f"{job_prefix}-{code[:20]}" if code else f"{job_prefix}-{seq:05d}"

            jobs.append(
                Job(
                    id=jid,
                    path=path,
                    title=title,
                    employer=f"{dept}/{_cell(row, cols.get('bureau'))}".strip("/"),
                    region_province=region_full[:12],
                    region_detail=region_full,
                    edu_require=parse_edu_require(_cell(row, cols.get("edu"))),
                    major_rules=parse_major_cell(major_text, catalogs),
                    political_req=parse_political(_cell(row, cols.get("political")))
                    or parse_political(remark if "党员" in remark else ""),
                    birth_after=birth,
                    age_max=age_max,
                    household_provinces=household,
                    fresh_only=fresh,
                    apply_deadline=apply_deadline,
                    quota=_to_int(_cell(row, cols.get("quota"))),
                    other_notes=notes,
                    source_url=source_url,
                )
            )
    return jobs


def _to_int(text: str) -> Optional[int]:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None
