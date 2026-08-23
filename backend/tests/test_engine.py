import unittest
from datetime import date

from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import load_catalogs
from app.matching.engine import Matcher, Verdict
from app.models.job import EduRequire, Job, MajorPolicy, parse_birth_after
from app.models.profile import (
    EducationRecord,
    EduLevel,
    FreshStatus,
    PoliticalStatus,
    UserProfile,
)


def make_profile(**kw) -> UserProfile:
    """完整档案：双一流本科计算机 + 双一流硕士计算机，党员，应届。"""
    base = dict(
        birth_date=date(1998, 7, 15),
        gender="女",
        political_status=PoliticalStatus.PARTY,
        fresh_status=FreshStatus.FRESH,
        is_student_cadre=True,
        has_school_award=True,
        household_province="广东省",
        origin_province="广东省",
        education=[
            EducationRecord(
                level=EduLevel.BACHELOR,
                major_name="计算机科学与技术",
                major_code="080901",
                school="某双一流大学",
                is_double_first_class=True,
            ),
            EducationRecord(
                level=EduLevel.MASTER,
                major_name="计算机科学与技术",
                major_code="0812",
                school="某双一流大学",
                is_double_first_class=True,
            ),
        ],
    )
    base.update(kw)
    return UserProfile(**base)


def make_job(**kw) -> Job:
    """基准岗位：省直机关，硕士及以上，计算机类，党员，应届。"""
    base = dict(
        id="test-001",
        path=3,
        title="省直机关一级主任科员以下岗位",
        employer="某省直机关",
        edu_require=EduRequire(min_level=EduLevel.MASTER),
        major_rules=[{"type": "prefix", "value": ["0812", "0835", "0839"], "scope": "academic"}],
        political_req=[PoliticalStatus.PARTY],
        fresh_only=False,
        apply_deadline=date(2026, 12, 10),
    )
    base.update(kw)
    return Job(**base)


class TestParseBirthAfter(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_birth_after("1998年7月以后出生"), date(1998, 7, 1))
        self.assertEqual(parse_birth_after("要求1999年11月1日及以后出生"), date(1999, 11, 1))
        self.assertIsNone(parse_birth_after("年龄要求35周岁以下"))
        self.assertIsNone(parse_birth_after(""))


class TestEngineEligible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = Matcher(load_catalogs(), load_aliases())

    def test_baseline_eligible(self):
        r = self.matcher.match(make_job(), make_profile())
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)
        self.assertTrue(all(x.status == "pass" for x in r.reasons))

    def test_xuandiao_full_eligible(self):
        # 选调生：应届+党员+双一流+学生干部+表彰
        job = make_job(
            path=1,
            fresh_only=True,
            require_double_first_class=True,
            require_student_cadre=True,
            require_award=True,
            birth_after=date(1998, 7, 1),
        )
        r = self.matcher.match(job, make_profile())
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)

    def test_major_by_any_degree_policy(self):
        # 事业单位（path=4 默认 any_degree）：本科计算机 + 硕士法学，岗位"计算机相关"
        job = make_job(
            path=4,
            edu_require=EduRequire(min_level=EduLevel.BACHELOR),
            major_rules=[{"type": "text", "value": "计算机相关"}],
            political_req=None,
        )
        p = make_profile(
            education=[
                EducationRecord(level=EduLevel.BACHELOR, major_name="计算机科学与技术", major_code="080901"),
                EducationRecord(level=EduLevel.MASTER, major_name="法学", major_code="0301"),
            ]
        )
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)  # 本科专业救了 any_degree 口径

    def test_major_by_highest_only_policy(self):
        # 同样档案，国考口径（path=2 默认 highest_only）：法学硕士报计算机岗 ❌
        job = make_job(
            path=2,
            major_rules=[{"type": "text", "value": "计算机相关"}],
            political_req=None,
        )
        p = make_profile(
            education=[
                EducationRecord(level=EduLevel.BACHELOR, major_name="计算机科学与技术", major_code="080901"),
                EducationRecord(level=EduLevel.MASTER, major_name="法学", major_code="0301"),
            ]
        )
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)
        major = next(x for x in r.reasons if x.field == "专业")
        self.assertIn("highest_only", major.detail)


class TestEngineIneligible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = Matcher(load_catalogs(), load_aliases())

    def test_gender_fail(self):
        job = make_job(gender_limit="男")
        r = self.matcher.match(job, make_profile())
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)
        g = next(x for x in r.reasons if x.field == "性别")
        self.assertIn("限男", g.detail)

    def test_birth_after_fail(self):
        job = make_job(birth_after=date(1999, 1, 1))
        p = make_profile(birth_date=date(1998, 7, 15))
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)
        a = next(x for x in r.reasons if x.field == "年龄")
        self.assertEqual(a.status, "fail")

    def test_birth_after_boundary_pass(self):
        job = make_job(birth_after=date(1998, 7, 1))
        p = make_profile(birth_date=date(1998, 7, 1))  # 边界当天
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)

    def test_age_max_at_deadline(self):
        # 截止日 2026-12-10，1990-12-20 出生 → 届时 35 周岁未满 36 → ≤35 通过
        job = make_job(age_max=35)
        p = make_profile(birth_date=date(1990, 12, 20))
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)
        # 1990-01-01 出生 → 届时 35 周岁已满 → 通过；1989-12-31 → 36 周岁 → 拒
        p2 = make_profile(birth_date=date(1989, 12, 31))
        r2 = self.matcher.match(job, p2)
        self.assertEqual(r2.verdict, Verdict.INELIGIBLE)
        a = next(x for x in r2.reasons if x.field == "年龄")
        self.assertIn("36周岁", a.detail)

    def test_edu_min_fail(self):
        job = make_job(edu_require=EduRequire(min_level=EduLevel.MASTER))
        p = make_profile(
            education=[
                EducationRecord(level=EduLevel.BACHELOR, major_name="计算机科学与技术", major_code="080901")
            ]
        )
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)
        e = next(x for x in r.reasons if x.field == "学历")
        self.assertIn("硕士及以上", e.detail)

    def test_edu_exact_bachelor_blocks_master(self):
        # "仅限本科"：硕士不可报（国考口径）
        job = make_job(
            edu_require=EduRequire(exact_level=EduLevel.BACHELOR),
            major_rules=[{"type": "prefix", "value": "0809", "scope": "undergraduate"}],
            political_req=None,
        )
        r = self.matcher.match(job, make_profile())
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)

    def test_major_fail_with_reason(self):
        job = make_job(major_rules=[{"type": "code", "value": ["0301"]}])
        r = self.matcher.match(job, make_profile())
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)
        m = next(x for x in r.reasons if x.field == "专业")
        self.assertIn("未命中", m.detail)

    def test_fresh_fail(self):
        job = make_job(fresh_only=True)
        p = make_profile(fresh_status=FreshStatus.NOT_FRESH)
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)

    def test_fresh_calm_period_pass_with_attention(self):
        job = make_job(fresh_only=True)
        p = make_profile(fresh_status=FreshStatus.CALM_PERIOD)
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)
        self.assertTrue(any("择业期" in a for a in r.attention))

    def test_political_candidate_counts_as_party(self):
        job = make_job(political_req=[PoliticalStatus.PARTY])
        p = make_profile(political_status=PoliticalStatus.CANDIDATE)
        r = self.matcher.match(job, p)
        pol = next(x for x in r.reasons if x.field == "政治面貌")
        self.assertEqual(pol.status, "pass")

    def test_political_fail(self):
        job = make_job(political_req=[PoliticalStatus.PARTY, PoliticalStatus.LEAGUE])
        p = make_profile(political_status=PoliticalStatus.MASSES)
        r = self.matcher.match(job, p)
        pol = next(x for x in r.reasons if x.field == "政治面貌")
        self.assertEqual(pol.status, "fail")

    def test_household_fail(self):
        job = make_job(household_provinces=["山东省", "浙江省"])
        r = self.matcher.match(job, make_profile())  # 广东户籍
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)
        h = next(x for x in r.reasons if x.field == "户籍/生源")
        self.assertIn("山东", h.detail)

    def test_household_origin_match(self):
        # 户籍不符但生源符合 → 通过
        job = make_job(household_provinces=["浙江省"])
        p = make_profile(household_province="广东省", origin_province="浙江省")
        r = self.matcher.match(job, p)
        h = next(x for x in r.reasons if x.field == "户籍/生源")
        self.assertEqual(h.status, "pass")

    def test_dfc_fail(self):
        job = make_job(require_double_first_class=True)
        p = make_profile(
            education=[
                EducationRecord(
                    level=EduLevel.MASTER,
                    major_name="计算机科学与技术",
                    major_code="0812",
                    is_double_first_class=False,
                )
            ]
        )
        r = self.matcher.match(job, p)
        d = next(x for x in r.reasons if x.field == "双一流")
        self.assertEqual(d.status, "fail")


class TestEngineInsufficient(unittest.TestCase):
    """⚠️信息不足：不误判 ✅/❌，并指出缺什么——产品核心体验。"""

    @classmethod
    def setUpClass(cls):
        cls.matcher = Matcher(load_catalogs(), load_aliases())

    def test_missing_political(self):
        job = make_job(political_req=[PoliticalStatus.PARTY])
        p = make_profile(political_status=None)
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)
        pol = next(x for x in r.reasons if x.field == "政治面貌")
        self.assertIn("未填", pol.detail)

    def test_missing_fresh(self):
        job = make_job(fresh_only=True)
        p = make_profile(fresh_status=None)
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)

    def test_missing_dfc(self):
        job = make_job(require_double_first_class=True)
        p = make_profile(
            education=[
                EducationRecord(level=EduLevel.MASTER, major_name="计算机科学与技术", major_code="0812")
            ]  # 未填双一流
        )
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)

    def test_missing_household(self):
        job = make_job(household_provinces=["广东省"])
        p = make_profile(household_province=None, origin_province=None)
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)

    def test_missing_major_code(self):
        job = make_job(major_rules=[{"type": "code", "value": "0812"}])
        p = make_profile(
            education=[
                EducationRecord(level=EduLevel.MASTER, major_name="计算机科学与技术", major_code="")
            ]
        )
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)
        m = next(x for x in r.reasons if x.field == "专业")
        self.assertIn("未填写专业代码", m.detail)

    def test_unknown_alias_insufficient(self):
        job = make_job(major_rules=[{"type": "text", "value": "航空航天相关"}], political_req=None)
        r = self.matcher.match(job, make_profile())
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)
        m = next(x for x in r.reasons if x.field == "专业")
        self.assertIn("未收录别名表", m.detail)

    def test_fail_wins_over_unknown(self):
        # 同时存在 fail 与 unknown → 判 ❌（fail 优先）
        job = make_job(gender_limit="男", political_req=[PoliticalStatus.PARTY])
        p = make_profile(gender="女", political_status=None)
        r = self.matcher.match(job, p)
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)

    def test_other_notes_surfaced(self):
        job = make_job(other_notes="需通过大学英语六级（CET-6）425分以上")
        r = self.matcher.match(job, make_profile())
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)
        self.assertTrue(any("CET-6" in a for a in r.attention))


if __name__ == "__main__":
    unittest.main()
