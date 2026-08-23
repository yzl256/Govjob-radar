import unittest
from datetime import date
from pathlib import Path

from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import load_catalogs
from app.knowledge.schools import load_schools
from app.matching.engine import Matcher, Verdict
from app.models.job import EduRequire, Job
from app.models.profile import EducationRecord, EduLevel, UserProfile

ROOT = Path(__file__).resolve().parents[2]


class TestSchoolTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = load_schools(ROOT)

    def test_official_count_147(self):
        self.assertEqual(len(self.t), 147)  # 教育部第二轮官方总数

    def test_exact_hits(self):
        for name in ("北京大学", "浙江大学", "石河子大学", "南方科技大学", "海军军医大学"):
            self.assertEqual(self.t.lookup(name), name)

    def test_paren_width_normalized(self):
        # 全角/半角括号等价
        self.assertEqual(self.t.lookup("中国石油大学(华东)"), "中国石油大学（华东）")
        self.assertEqual(self.t.lookup("中国地质大学(北京)"), "中国地质大学（北京）")

    def test_aliases(self):
        cases = {
            "人大": "中国人民大学",
            "中科大": "中国科学技术大学",
            "国科大": "中国科学院大学",
            "哈工大": "哈尔滨工业大学",
            "北邮": "北京邮电大学",
            "西电": "西安电子科技大学",
            "华电": "华北电力大学",
            "上财": "上海财经大学",
            "第二军医大学": "海军军医大学",  # 更名前校名
            "上海体育大学": "上海体育学院",  # 2023 更名后
        }
        for alias, canonical in cases.items():
            self.assertEqual(self.t.lookup(alias), canonical, alias)

    def test_suffix_substring_allowed(self):
        # 以大学/学院结尾且≥4字：允许作为官方校名前缀（华东/北京校区均双一流，任一命中即可）
        self.assertTrue(self.t.lookup("中国石油大学") in ("中国石油大学（华东）", "中国石油大学（北京）"))
        self.assertIsNone(self.t.lookup("中国科学技术"))  # 不以大学结尾 → 未知
        self.assertIsNone(self.t.lookup("广东大学"))  # 不存在的前缀 → 未知

    def test_independent_college_never_matches(self):
        # 独立学院绝不能因母体校名而误判
        self.assertIsNone(self.t.lookup("浙江大学城市学院"))
        self.assertIsNone(self.t.lookup("北京航空航天大学北海学院"))
        self.assertIsNone(self.t.lookup("某不知名学院"))
        self.assertIsNone(self.t.lookup(""))  # 空输入

    def test_ambiguous_shorthand_unknown(self):
        # 歧义简称（东大=东北/东南，海大=大连海事/中国海大/海南）宁可未知
        self.assertIsNone(self.t.lookup("东大"))
        self.assertIsNone(self.t.lookup("海大"))


class TestEngineDfcInference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = Matcher(load_catalogs(ROOT), load_aliases(ROOT), load_schools(ROOT))

    def _profile(self, school="", declared=None, level=EduLevel.MASTER):
        return UserProfile(
            birth_date=None,
            gender=None,
            education=[
                EducationRecord(
                    level=level,
                    major_name="大数据技术与工程",
                    major_code="085411",
                    catalog="professional",
                    school=school,
                    is_double_first_class=declared,
                )
            ],
        )

    def _job(self):
        return Job(
            path=5,  # 人才引进常限双一流
            title="引进生",
            require_double_first_class=True,
            edu_require=EduRequire(min_level=EduLevel.MASTER),
            major_rules=[{"type": "any"}],
        )

    def test_school_inferred_pass(self):
        r = self.matcher.match(self._job(), self._profile(school="哈工大"), today=date(2026, 8, 20))
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)
        dfc = next(x for x in r.reasons if x.field == "双一流")
        self.assertEqual(dfc.status, "pass")
        self.assertIn("名单内", dfc.detail)

    def test_unknown_school_unknown_not_fail(self):
        r = self.matcher.match(
            self._job(), self._profile(school="浙大城院"), today=date(2026, 8, 20)
        )
        self.assertEqual(r.verdict, Verdict.INSUFFICIENT)
        dfc = next(x for x in r.reasons if x.field == "双一流")
        self.assertEqual(dfc.status, "unknown")
        self.assertIn("核对", dfc.detail)

    def test_declared_false_fails(self):
        r = self.matcher.match(
            self._job(), self._profile(declared=False), today=date(2026, 8, 20)
        )
        self.assertEqual(r.verdict, Verdict.INELIGIBLE)
        dfc = next(x for x in r.reasons if x.field == "双一流")
        self.assertEqual(dfc.status, "fail")

    def test_declared_true_beats_table(self):
        r = self.matcher.match(
            self._job(), self._profile(school="某学院", declared=True), today=date(2026, 8, 20)
        )
        self.assertEqual(r.verdict, Verdict.ELIGIBLE)

    def test_no_school_unknown(self):
        r = self.matcher.match(self._job(), self._profile(), today=date(2026, 8, 20))
        dfc = next(x for x in r.reasons if x.field == "双一流")
        self.assertEqual(dfc.status, "unknown")


if __name__ == "__main__":
    unittest.main()
