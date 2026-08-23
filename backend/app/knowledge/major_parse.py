# 专业写法解析器：公告"专业"列文本 → List[MajorRule]
# 处理五种写法（设计文档 §8.2）：
#   1. 不限/空白 → any
#   2. 带代码：'0812计算机科学与技术' / '计算机类（0809）' → code
#   3. 类/门类名：'计算机类' → prefix（查本科目录类节点）
#   4. 纯专业名：'软件工程' → code（按名称在三个目录查，多处命中则 OR）
#   5. 模糊写法：'计算机相关' → text（进别名表，未收录判 ⚠️）
from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.knowledge.catalogs import (
    CATALOG_ACADEMIC,
    CATALOG_PROFESSIONAL,
    CATALOG_UNDERGRAD,
    Catalog,
    normalize_code,
)
from app.models.job import MajorRule, MajorRuleType

_SPLIT_RE = re.compile(r"[、，,;；/／\n\t]+|\s{2,}")
_CODE_RE = re.compile(r"(\d{4}|\d{6})")
_PAREN_CODE_RE = re.compile(r"[（(]\s*(?:学科代码|专业代码)?\s*[:：]?\s*([0-9]{4}(?:\s*[、,，/]\s*[0-9]{4})*)\s*[)）]")


def _norm(text: str) -> str:
    return (text or "").replace("\u00a0", " ").strip()


def parse_major_cell(
    cell: Optional[str],
    catalogs: Dict[str, Catalog],
) -> List[MajorRule]:
    rules: List[MajorRule] = []
    if not _norm(cell) or "不限" in cell or "无要求" in cell or cell.strip() in ("--", "—"):
        return [MajorRule(type=MajorRuleType.ANY)]

    undergrad = catalogs[CATALOG_UNDERGRAD]
    academic = catalogs[CATALOG_ACADEMIC]
    professional = catalogs[CATALOG_PROFESSIONAL]

    # 先提取括号内成组代码："计算机类（0809）" / "（学科代码：0301、0302）"
    paren_codes: List[str] = []
    for m in _PAREN_CODE_RE.finditer(cell):
        for code in re.findall(r"\d{4}", m.group(1)):
            paren_codes.append(code)

    for token in (t for t in _SPLIT_RE.split(_norm(cell)) if _norm(t)):
        name = re.sub(_PAREN_CODE_RE, "", token).strip("：:、 　")
        # 整对括号包裹的 token 剥壳（"（计算机类）"→"计算机类"）；
        # 半开括号保留原文（"哲学(A01)" 广东目录代码照抄，进 TEXT 规则）
        if name[:1] in "（(" and name[-1:] in "）)":
            name = name[1:-1].strip("：:、 　")

        if "不限" in token:
            return [MajorRule(type=MajorRuleType.ANY)]

        # 写法2：代码开头（6位优先于4位，避免 080901 被截成 0809）
        m = re.match(r"^(\d{6}|\d{4})", _norm(token))
        if m:
            code = normalize_code(m.group(1))
            scope = CATALOG_UNDERGRAD if len(code) == 6 else None
            if not any(
                r.type == MajorRuleType.CODE and r.value == code for r in rules
            ):
                rules.append(
                    MajorRule(type=MajorRuleType.CODE, value=code, scope=scope)
                )
            continue

        # 写法3：类/门类名
        if name.endswith("类"):
            hit_u = next(
                (c for c, n in undergrad.class_nodes.items() if n == name), None
            )
            if hit_u:
                if not any(
                    r.type == MajorRuleType.PREFIX and r.value == hit_u for r in rules
                ):
                    rules.append(
                        MajorRule(
                            type=MajorRuleType.PREFIX,
                            value=hit_u,
                            scope=CATALOG_UNDERGRAD,
                        )
                    )
                continue

        # 写法4：纯专业名 → 各目录按名查代码
        hits = []
        for c in (c for c in undergrad.majors if undergrad.majors[c] == name):
            hits.append((CATALOG_UNDERGRAD, c))
        for c in (c for c in academic.majors if academic.majors[c] == name):
            hits.append((CATALOG_ACADEMIC, c))
        for c in (c for c in professional.majors if professional.majors[c] == name):
            hits.append((CATALOG_PROFESSIONAL, c))
        if hits:
            for scope, code in hits:
                if not any(
                    r.type == MajorRuleType.CODE and r.value == code for r in rules
                ):
                    rules.append(
                        MajorRule(type=MajorRuleType.CODE, value=code, scope=scope)
                    )
            continue

        # 写法5：模糊写法 → 交给别名表
        if name:
            rules.append(MajorRule(type=MajorRuleType.TEXT, value=name))

    # 括号里的成组代码并入（有时正文只写名称、代码在括号）
    for code in paren_codes:
        if not any(r.type == MajorRuleType.CODE and r.value == code for r in rules):
            rules.append(MajorRule(type=MajorRuleType.CODE, value=code, scope=None))

    if not rules:
        return [MajorRule(type=MajorRuleType.ANY)]
    return rules
