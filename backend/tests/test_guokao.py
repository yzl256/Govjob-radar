import unittest
from datetime import date

from app.crawler.guokao import (
    locate_header,
    parse_edu_require,
    parse_guokao_workbook,
    parse_political,
)
from app.io.xlsx import write_workbook
from app.knowledge.catalogs import load_catalogs
from app.models.job import MajorRuleType
from app.models.profile import EduLevel, PoliticalStatus

HEADER = ["部门名称", "用人司局", "招考职位", "职位简介", "招考人数", "专业", "学历", "学位",
          "政治面貌", "基层工作最低年限", "服务基层项目工作经历", "工作地点", "备注"]

ROW = lambda **kw: [  # noqa: E731
    kw.get("dept", "外交部"), kw.get("bureau", "信息处"), kw.get("title", "科员"),
    kw.get("intro", ""), kw.get("quota", "2"), kw.get("major", "不限"),
    kw.get("edu", "本科及以上"), kw.get("degree", "学士"), kw.get("political", "不限"),
    kw.get("grassroots", "无要求"), kw.get("service", "无要求"),
    kw.get("place", "北京市"), kw.get("remark", ""),
]


class TestHeaderLocate(unittest.TestCase):
    def test_locate_skips_title_row(self):
        rows = [["某某年度考试录用公务员职位表"], HEADER, ROW()]
        self.assertEqual(locate_header(rows), 1)

    def test_no_header(self):
        self.assertIsNone(locate_header([["a", "b"], ["c", "d"]]))


class TestEduParse(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(parse_edu_require("仅限本科").exact_level, EduLevel.BACHELOR)
        self.assertEqual(parse_edu_require("硕士研究生及以上").min_level, EduLevel.MASTER)
        self.assertEqual(parse_edu_require("本科及以上").min_level, EduLevel.BACHELOR)
        self.assertEqual(parse_edu_require("大专及以上").min_level, EduLevel.COLLEGE)
        self.assertEqual(parse_edu_require("博士研究生及以上").min_level, EduLevel.DOCTOR)
        band = parse_edu_require("大专或本科")
        self.assertEqual((band.min_level, band.max_level), (EduLevel.COLLEGE, EduLevel.BACHELOR))
        band2 = parse_edu_require("本科或硕士研究生（含）")
        self.assertEqual((band2.min_level, band2.max_level), (EduLevel.BACHELOR, EduLevel.MASTER))


class TestPoliticalParse(unittest.TestCase):
    def test_variants(self):
        self.assertIsNone(parse_political("不限"))
        self.assertEqual(parse_political("中共党员"), [PoliticalStatus.PARTY])
        self.assertEqual(
            parse_political("中共党员或共青团员"),
            [PoliticalStatus.PARTY, PoliticalStatus.LEAGUE],
        )
        self.assertIsNone(parse_political("民主党派成员"))  # 复杂口径不硬解析


class TestWorkbookParse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = load_catalogs()
        rows = [
            ["标题行：中央机关及其直属机构考试录用公务员招考职位表（样例）"],
            HEADER,
            ROW(title="网络安全岗", major="0812计算机科学与技术、0835软件工程",
                edu="硕士研究生及以上", degree="硕士", political="中共党员",
                remark="限应届高校毕业生", place="北京市"),
            ROW(dept="国家税务总局", title="执法员", major="计算机类（0809）",
                edu="本科及以上", quota="3", place="广东省深圳市"),
            ROW(dept="司法部", title="刑罚执行", major="0301法学",
                political="中共党员或共青团员", grassroots="二年",
                remark="适合男性，需通过CET-6", place="山东省济南市"),
            ["", "", "", "", "", "", "", "", "", "", "", "", ""],  # 空行跳过
        ]
        write_workbook("t_gk.xlsx", "职位表", rows)
        cls.jobs = parse_guokao_workbook(
            "t_gk.xlsx", cls.catalogs, source_url="https://example.gov.cn/x",
            apply_deadline=date(2026, 10, 25),
        )

    def test_row_count_and_ids(self):
        self.assertEqual(len(self.jobs), 3)
        self.assertEqual([j.id for j in self.jobs], ["gk-00001", "gk-00002", "gk-00003"])

    def test_field_mapping(self):
        j = self.jobs[0]
        self.assertEqual(j.path, 2)
        self.assertEqual(j.title, "网络安全岗")
        self.assertEqual(j.employer, "外交部/信息处")
        self.assertEqual(j.region_detail, "北京市")
        self.assertEqual(j.quota, 2)
        self.assertEqual(j.edu_require.min_level, EduLevel.MASTER)
        self.assertEqual(j.political_req, [PoliticalStatus.PARTY])
        self.assertTrue(j.fresh_only)
        self.assertIn("备注", j.other_notes)
        self.assertEqual(j.apply_deadline, date(2026, 10, 25))

    def test_major_rules_mapping(self):
        j0 = self.jobs[0]
        codes = {r.value for r in j0.major_rules if r.type == MajorRuleType.CODE}
        self.assertEqual(codes, {"0812", "0835"})
        j1 = self.jobs[1]
        prefixes = [r for r in j1.major_rules if r.type == MajorRuleType.PREFIX]
        self.assertEqual(prefixes[0].value, "0809")

    def test_law_major_codes(self):
        j2 = self.jobs[2]
        codes = {r.value for r in j2.major_rules}
        self.assertIn("0301", codes)

    def test_other_notes_aggregation(self):
        j = self.jobs[2]
        self.assertIn("基层工作最低年限：二年", j.other_notes)
        self.assertIn("CET-6", j.other_notes)
        # 服务基层无要求不应出现
        self.assertNotIn("服务基层项目经历", j.other_notes)

    def test_fresh_flag_from_intro(self):
        rows = [["t"], HEADER, ROW(intro="限应届高校毕业生报考")]
        write_workbook("t_gk2.xlsx", "s", rows)
        jobs = parse_guokao_workbook("t_gk2.xlsx", self.catalogs)
        self.assertTrue(jobs[0].fresh_only)


if __name__ == "__main__":
    unittest.main()
