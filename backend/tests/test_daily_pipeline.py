import unittest
from datetime import date

from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import load_catalogs
from app.matching.engine import Matcher, Verdict
from app.models.profile import (
    EducationRecord,
    EduLevel,
    PoliticalStatus,
    UserProfile,
)
from app.pipeline.daily import build_report


def profile(**kw) -> UserProfile:
    base = dict(
        name="demo",
        birth_date=date(1998, 7, 15),
        gender="女",
        political_status=PoliticalStatus.PARTY,
        household_province="广东省",
        origin_province="广东省",
        education=[
            EducationRecord(level=EduLevel.BACHELOR, major_name="计算机科学与技术", major_code="080901"),
            EducationRecord(level=EduLevel.MASTER, major_name="计算机科学与技术", major_code="0812"),
        ],
    )
    base.update(kw)
    return UserProfile(**base)


class TestDailyPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = load_catalogs()
        cls.matcher = Matcher(cls.catalogs, load_aliases())

    def test_grouping_and_render(self):
        from app.models.job import EduRequire, Job

        jobs = [
            Job(id="a", path=2, title="网安岗", employer="外交部",
                edu_require=EduRequire(min_level=EduLevel.MASTER),
                major_rules=[{"type": "code", "value": "0812"}],
                apply_deadline=date(2026, 10, 25),
                quota=2, region_detail="北京市", source_url="u"),
            Job(id="b", path=2, title="法律岗", employer="司法部",
                edu_require=EduRequire(min_level=EduLevel.BACHELOR),
                major_rules=[{"type": "code", "value": "0301"}]),
            Job(id="c", path=2, title="机要岗", employer="办公厅",
                edu_require=EduRequire(min_level=EduLevel.BACHELOR),
                major_rules=[{"type": "code", "value": "0812"}],
                political_req=[PoliticalStatus.PARTY]),  # 政治面貌缺失场景
        ]
        p = profile(political_status=None)  # 缺政治面貌 → c 条 ⚠️
        report = build_report(p, jobs, self.matcher, today=date(2026, 8, 20))

        self.assertEqual(len(report.eligible), 1)      # a：全过
        self.assertEqual(len(report.insufficient), 1)  # c：缺政治面貌
        self.assertEqual(report.ineligible_count, 1)   # b：专业不符

        text = report.render_text()
        self.assertIn("可报 1 条 | 信息不足 1 条 | 不可报 1 条", text)
        self.assertIn("网安岗", text)
        self.assertIn("截止 2026-10-25", text)
        self.assertIn("缺：", text)
        self.assertIn("专业×1", text)  # 不可报主因统计

    def test_empty_jobs_report(self):
        report = build_report(profile(), [], self.matcher, today=date(2026, 8, 20))
        self.assertEqual(len(report.eligible), 0)
        text = report.render_text()
        self.assertIn("可报（0）", text)

    def test_eligible_cap_in_render(self):
        from app.models.job import Job

        jobs = [
            Job(id=f"j{i}", path=2, title=f"岗{i}", employer="X",
                apply_deadline=date(2026, 10, 25))  # 无任何限制 → 全可报
            for i in range(12)
        ]
        report = build_report(profile(), jobs, self.matcher, today=date(2026, 8, 20))
        self.assertEqual(len(report.eligible), 12)
        text = report.render_text()
        self.assertIn("另有 2 条", text)


if __name__ == "__main__":
    unittest.main()
