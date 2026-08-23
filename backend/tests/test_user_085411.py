import unittest
from datetime import date

from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import (
    CATALOG_PROFESSIONAL,
    infer_catalog,
    load_catalogs,
)
from app.matching.engine import Matcher, Verdict
from app.matching.majors import build_candidates
from app.models.job import EduRequire, Job, MajorPolicy
from app.models.profile import EducationRecord, EduLevel, UserProfile

# 用户真实数据：硕士 · 大数据技术与工程 · 085411 · 专业学位（电子信息 0854 类别下领域）
USER_EDU = dict(
    level=EduLevel.MASTER,
    major_name="大数据技术与工程",
    major_code="085411",
    catalog="professional",
)


def user_profile(**kw) -> UserProfile:
    base = dict(
        birth_date=None,
        gender=None,
        fresh_status="择业期内未落实工作",
        education=[EducationRecord(**USER_EDU)],
    )
    base.update(kw)
    return UserProfile(**base)


class TestUser085411(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = load_catalogs()
        cls.matcher = Matcher(cls.catalogs, load_aliases())

    def test_catalog_contains_085411(self):
        p = self.catalogs[CATALOG_PROFESSIONAL]
        self.assertEqual(p.majors["085411"], "大数据技术与工程")
        self.assertEqual(p.parents["085411"], "0854")

    def test_infer_catalog_field_code(self):
        self.assertEqual(infer_catalog("硕士", "085411", self.catalogs), CATALOG_PROFESSIONAL)

    def test_candidate_expands_parent(self):
        cands, missing = build_candidates(
            user_profile(), EduLevel.BACHELOR, None, MajorPolicy.HIGHEST_ONLY, self.catalogs
        )
        self.assertIsNone(missing)
        codes = sorted(c.code for c in cands)
        self.assertEqual(codes, ["0854", "085411"])  # 领域码 + 母类别双候选
        self.assertTrue(all(c.catalog == CATALOG_PROFESSIONAL for c in cands))

    def test_job_rule_category_0854_hits(self):
        # 岗位写"0854电子信息"（类别码）→ 领域码考生命中
        job = Job(
            path=2,
            title="信息化岗",
            edu_require=EduRequire(min_level=EduLevel.MASTER),
            major_rules=[{"type": "code", "value": "0854", "scope": "professional"}],
        )
        r = self.matcher.match(job, user_profile(), today=date(2026, 8, 20))
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)

    def test_job_rule_undergrad_computer_class_hits_via_family(self):
        # 岗位写"计算机类（0809）·本科及以上"（国考口径 HIGHEST_ONLY）→ 类族映射 0854 命中
        job = Job(
            path=2,
            title="行政执法员",
            edu_require=EduRequire(min_level=EduLevel.BACHELOR),
            major_rules=[{"type": "prefix", "value": "0809", "scope": "undergraduate"}],
        )
        r = self.matcher.match(job, user_profile(), today=date(2026, 8, 20))
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)

    def test_job_rule_alias_computer_hits(self):
        job = Job(
            path=4,  # 事业编 any_degree 口径
            title="技术岗",
            edu_require=EduRequire(min_level=EduLevel.MASTER),
            major_rules=[{"type": "text", "value": "计算机相关"}],
        )
        r = self.matcher.match(job, user_profile(), today=date(2026, 8, 20))
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)

    def test_job_rule_law_misses(self):
        job = Job(
            path=2,
            title="法律岗",
            edu_require=EduRequire(min_level=EduLevel.BACHELOR),
            major_rules=[{"type": "code", "value": "0301", "scope": "academic"}],
        )
        r = self.matcher.match(job, user_profile(), today=date(2026, 8, 20))
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)

    def test_missing_birth_gender_yield_unknown(self):
        # 档案只有学历信息：限性别/年龄的岗位 → ⚠️ 而非崩溃或误判
        job = Job(
            path=2,
            title="机要岗",
            gender_limit="女",
            birth_after=date(1998, 7, 1),
            edu_require=EduRequire(min_level=EduLevel.MASTER),
            major_rules=[{"type": "any"}],
        )
        r = self.matcher.match(job, user_profile(), today=date(2026, 8, 20))
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)
        fields = {x.field: x for x in r.reasons}
        self.assertEqual(fields["性别"].status, "unknown")
        self.assertEqual(fields["年龄"].status, "unknown")

    def test_fresh_calm_period_attention(self):
        job = Job(
            path=2,
            title="应届岗",
            fresh_only=True,
            edu_require=EduRequire(min_level=EduLevel.MASTER),
            major_rules=[{"type": "any"}],
        )
        r = self.matcher.match(job, user_profile(), today=date(2026, 8, 20))
        # 2026年6月毕业、择业期内：fresh 通过，但提示口径以公告为准
        self.assertTrue(any("择业期" in a for a in r.attention))


if __name__ == "__main__":
    unittest.main()
