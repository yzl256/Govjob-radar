# 专业匹配：把岗位的 major_rules 与用户候选代码比对
# 三态输出：True=符合 / False=不符合 / None=信息不足
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple

from app.knowledge.alias import AliasTable
from app.knowledge.catalogs import Catalog, normalize_code
from app.models.job import MajorPolicy, MajorRule, MajorRuleType
from app.models.profile import LEVEL_ORDER, EducationRecord, EduLevel, UserProfile


class Candidate(NamedTuple):
    catalog: str
    code: str  # normalized
    level: str  # "本科"/"硕士"/"博士"/"大专"


def build_candidates(
    profile: UserProfile,
    lo: Optional[EduLevel],
    hi: Optional[EduLevel],
    policy: MajorPolicy,
    catalogs: Dict[str, Catalog],
) -> Tuple[List[Candidate], Optional[str]]:
    """按学历区间 + 报考口径筛选候选 (目录, 代码)。
    返回 (candidates, 缺失说明)。缺失说明非空 → 专业判定为"信息不足"。"""
    records: List[EducationRecord] = profile.records_in_band(lo, hi)
    if not records:
        levels = f"{lo.value if lo else '?'}~{hi.value if hi else '?'}"
        return [], f"档案中没有 {levels} 层次的学历记录"

    if policy == MajorPolicy.HIGHEST_ONLY:
        top = max(LEVEL_ORDER[r.level] for r in records)
        records = [r for r in records if LEVEL_ORDER[r.level] == top]

    candidates: List[Candidate] = []
    missing: List[str] = []
    for r in records:
        if not normalize_code(r.major_code):
            missing.append(f"{r.level.value}学历未填写专业代码")
            continue
        catalog = r.catalog or _infer(r, catalogs)
        if catalog is None:
            missing.append(f"{r.level.value}学历({r.major_name or r.major_code})不在已建目录范围（如专科）")
            continue
        code = normalize_code(r.major_code)
        candidates.append(Candidate(catalog, code, r.level.value))
        # 专业学位6位领域码（如 085411 大数据技术与工程）→ 追加母类别 0854 候选，
        # 使"0854电子信息"类岗位规则能直接命中领域码考生
        parent = catalogs[catalog].parents.get(code)
        if parent and parent in catalogs[catalog].majors:
            candidates.append(Candidate(catalog, parent, r.level.value))

    if not candidates and missing:
        return [], "；".join(missing)
    return candidates, None


def _infer(record: EducationRecord, catalogs: Dict[str, Catalog]) -> Optional[str]:
    from app.knowledge.catalogs import infer_catalog

    return infer_catalog(record.level.value, record.major_code, catalogs)


def eval_major_rules(
    rules: List[MajorRule],
    candidates: List[Candidate],
    aliases: AliasTable,
) -> Tuple[Optional[bool], str]:
    """规则间 OR。任一 True → True；否则任一 None → None；全 False → False。

    跨目录语义：
    - scope=undergraduate 的"类"要求遇研究生考生 → 查类族映射（class_families）：
      命中同族代码 ✅；类族已收录但不命中 ❌；类族未收录 ⚠️（宁缺勿滥）。
    - scope=undergraduate 的精确代码遇研究生考生（岗位学历带含本科）→ ⚠️
      （本科6位代码无法直接对应研究生代码，除非公告列出研究生代码）。
    """
    if not rules or any(r.type == MajorRuleType.ANY for r in rules):
        return True, "专业不限"

    missing_codes = not candidates
    results: List[Optional[bool]] = []
    details: List[str] = []

    for rule in rules:
        vals = rule.value if isinstance(rule.value, list) else ([rule.value] if rule.value else [])
        scope = rule.scope

        if rule.type == MajorRuleType.CODE:
            if missing_codes:
                results.append(None)
                details.append("规则[精确代码]无法判定：缺专业代码")
                continue
            want = {normalize_code(v) for v in vals}
            hit, unresolved = [], []
            for c in candidates:
                if c.code in want and (scope is None or c.catalog == scope):
                    hit.append(c)
                elif scope == "undergraduate" and c.catalog in ("academic", "professional"):
                    unresolved.append(c)
            results.append(bool(hit) if hit or not unresolved else None)
            if hit or not unresolved:
                details.append(f"规则[精确代码 {'/'.join(sorted(want))}] {'命中' if hit else '未命中'}")
            else:
                details.append(
                    f"规则[精确代码 {'/'.join(sorted(want))}] 为本科目录代码，研究生学历无法直接对应，需人工确认"
                )

        elif rule.type == MajorRuleType.PREFIX:
            if missing_codes:
                results.append(None)
                details.append("规则[专业类/门类]无法判定：缺专业代码")
                continue
            want = [normalize_code(v) for v in vals if normalize_code(v)]
            hit, unresolved = [], []
            for c in candidates:
                matched = False
                if c.code.startswith(tuple(want)) and (scope is None or c.catalog == scope):
                    matched = True
                elif scope == "undergraduate" and c.catalog in ("academic", "professional"):
                    # 跨目录"类"解释：按类族映射
                    fam_known = True
                    for w in want:
                        fam = aliases.class_family(w)
                        if fam is None:
                            fam_known = False
                            continue
                        if c.code in fam.get(c.catalog, set()):
                            matched = True
                            break
                    if not matched and fam_known is False and len(want) == 1:
                        unresolved.append(c)
                if matched:
                    hit.append(c)
            results.append(bool(hit) if hit or not unresolved else None)
            if hit or not unresolved:
                details.append(f"规则[类/门类 {'/'.join(want)}] {'命中' if hit else '未命中'}")
            else:
                details.append(
                    f"规则[类 {'/'.join(want)}] 本科目录与研究生目录的对应关系未收录，需人工确认"
                )

        elif rule.type == MajorRuleType.TEXT:
            expansion = aliases.get(str(rule.value or ""))
            if expansion is None:
                # 未知别名：无法判定（宁⚠️不误判）
                results.append(None)
                details.append(f"规则[模糊写法「{rule.value}」] 未收录别名表，需人工确认")
                continue
            if missing_codes:
                results.append(None)
                details.append("规则[模糊写法]无法判定：缺专业代码")
                continue
            hit = []
            for c in candidates:
                spec = expansion.get(c.catalog)
                if not spec:
                    continue
                if c.code in spec["codes"] or any(c.code.startswith(p) for p in spec["prefixes"]):
                    hit.append(c)
                    break
            results.append(bool(hit))
            detail_vals = "；".join(
                f"{cat}: 代码{sorted(s['codes']) or []}/前缀{sorted(s['prefixes']) or []}"
                for cat, s in expansion.items()
            )
            details.append(f"规则[模糊写法「{rule.value}」→ {detail_vals}] {'命中' if hit else '未命中'}")

    if any(r is True for r in results):
        verdict = True
    elif any(r is None for r in results):
        verdict = None
    else:
        verdict = False
    picked = next((d for r, d in zip(results, details) if r is True), None)
    detail = picked or "；".join(details)
    return verdict, detail
