# 职位表附件展开：链接发现、zip 解包、解析器通用化（省考/事业口径）的测试。
import unittest
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.crawler.guokao import parse_guokao_workbook
from app.io.xlsx import write_workbook
from app.knowledge.catalogs import load_catalogs
from app.models.profile import EduLevel
from app.pipeline.c_extract import (
    _extract_xlsx_from_zip,
    discover_attachment_links,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = load_catalogs(ROOT)


class TestAttachmentLinks(unittest.TestCase):
    def test_xlsx_zip_found_xls_ignored(self):
        html = """
        <a href="/att/a.xlsx">岗位表</a>
        <a href="http://x.com/b.zip?download=1">附件包</a>
        <a href="/att/c.xls">老格式</a>
        <a href="/att/d.pdf">简章</a>
        """
        links = discover_attachment_links("http://x.com/page.html", html)
        self.assertEqual(links, ["http://x.com/att/a.xlsx", "http://x.com/b.zip"])

    def test_query_fragment_stripped_and_dedupe(self):
        html = '<a href="/a.xlsx#t1">1</a><a href="/a.xlsx">2</a><a href="b.xlsx">3</a>'
        links = discover_attachment_links("http://x.com/p/", html)
        self.assertEqual(links, ["http://x.com/a.xlsx", "http://x.com/p/b.xlsx"])

    def test_excel_source_follows_announcement_page_for_attachment(self):
        """省考入口通常只列公告；职位表挂在公告详情页，不能只扫首页。"""
        import app.crawler.fetch as fetch

        pages = {
            "https://example.test/list": '<a href="/notice/1">2026年公务员招考公告</a>'.encode(),
            "https://example.test/notice/1": '<a href="/files/jobs.xlsx">职位表</a>'.encode(),
        }
        seen = []
        original = fetch.fetch_url
        fetch.fetch_url = lambda url, timeout=20: seen.append(url) or pages[url]
        try:
            source = SimpleNamespace(id="zj-shengkao", entry="https://example.test/list")
            links = fetch.discover_source_xlsx_links(source)
        finally:
            fetch.fetch_url = original

        self.assertEqual(links, ["https://example.test/files/jobs.xlsx"])
        self.assertEqual(seen, ["https://example.test/list", "https://example.test/notice/1"])


class TestZipExtract(unittest.TestCase):
    def test_only_xlsx_members_extracted(self):
        write_workbook("z_in.xlsx", "职位表", [["部门名称", "职位名称"]])
        Path("z_in.pdf").write_bytes(b"%PDF fake")
        with zipfile.ZipFile("z_pack.zip", "w") as z:
            z.write("z_in.xlsx", "docs/附件1-岗位表.xlsx")
            z.write("z_in.pdf", "docs/简章.pdf")
        out = _extract_xlsx_from_zip(Path("z_pack.zip"), Path("."))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].name.startswith("z_pack__"))

    def test_bad_zip_tolerated(self):
        Path("z_bad.zip").write_bytes(b"not a zip")
        self.assertEqual(_extract_xlsx_from_zip(Path("z_bad.zip"), Path(".")), [])


class TestGenericParser(unittest.TestCase):
    """省级职位表（省考/事业单位口径）：列别名、年龄、户籍、id 前缀、path。"""

    def setUp(self):
        write_workbook(
            "t_prov.xlsx",
            "岗位表",
            [
                ["2026年广东省事业单位集中公开招聘岗位表（样例）", "", "", "", "", ""],
                ["招聘单位", "具体单位", "招聘岗位", "招聘人数", "学历要求", "专业要求"],
                [
                    "广东省科学院", "自动化所", "大数据开发岗", "2", "硕士研究生及以上",
                    "电子信息类（0854）",
                ],
                [
                    "广州市卫健委", "市一医院", "信息科科员", "1", "本科及以上",
                    "计算机类、电子信息类",
                ],
            ],
        )

    def test_headers_age_household_columns(self):
        write_workbook(
            "t_prov2.xlsx",
            "s",
            [
                ["用人单位", "岗位名称", "需求人数", "学历层次", "专业名称", "年龄要求", "户籍要求"],
                ["深圳某局", "科员", "1", "硕士研究生", "085411 大数据技术与工程", "30周岁以下", "限广东省户籍"],
            ],
        )
        jobs = parse_guokao_workbook(
            "t_prov2.xlsx", CATALOGS, path=3, job_prefix="gd-sk"
        )
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j.id, "gd-sk-00001")  # 前缀隔离，不与国考 gk- 撞车
        self.assertEqual(j.path, 3)
        self.assertEqual(j.age_max, 30)
        self.assertIsNone(j.birth_after)
        self.assertEqual(j.household_provinces, ["广东省"])
        self.assertEqual(j.edu_require.min_level, EduLevel.MASTER)

    def test_birth_after_style_age(self):
        write_workbook(
            "t_prov3.xlsx",
            "s",
            [
                ["招聘单位", "岗位名称", "学历要求", "专业要求", "年龄要求"],
                ["某市委组织部", "选调生岗", "硕士研究生", "不限", "1998年7月以后出生"],
            ],
        )
        jobs = parse_guokao_workbook("t_prov3.xlsx", CATALOGS, path=1, job_prefix="sd-xd")
        self.assertEqual(jobs[0].birth_after, date(1998, 7, 1))

    def test_province_sheet_generic_columns(self):
        jobs = parse_guokao_workbook(
            "t_prov.xlsx", CATALOGS, source_url="http://x/post_1.html",
            apply_deadline=date(2026, 9, 30), path=4, job_prefix="gd-sydw-att",
        )
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].employer, "广东省科学院/自动化所")
        self.assertEqual(jobs[0].apply_deadline, date(2026, 9, 30))  # 计划级截止日回填
        self.assertEqual(jobs[0].path, 4)
        self.assertTrue(jobs[0].id.startswith("gd-sydw-att-"))
        self.assertTrue(jobs[1].major_rules)  # 计算机类解析出规则

    def test_city_only_household_goes_to_notes(self):
        write_workbook(
            "t_prov4.xlsx",
            "s",
            [
                ["招聘单位", "岗位名称", "学历要求", "专业要求", "户籍要求"],
                ["东莞某局", "科员", "本科", "不限", "限东莞市户籍"],
            ],
        )
        jobs = parse_guokao_workbook("t_prov4.xlsx", CATALOGS, path=4, job_prefix="t")
        self.assertIsNone(jobs[0].household_provinces)  # 不硬解析
        self.assertIn("东莞市", jobs[0].other_notes)  # 透出给用户


class TestHouseholdNormalize(unittest.TestCase):
    def test_prov_suffix_normalized(self):
        from app.matching.engine import Matcher
        from app.models.profile import UserProfile

        m = Matcher(CATALOGS, None)
        job = SimpleNamespace(household_provinces=["广东省", "浙江省"])
        p = UserProfile(name="x", education=[], household_province="广东")
        r = m._household(job, p)
        self.assertEqual(r.status, "pass")  # '广东'=='广东省' 归一化后命中

    def test_mismatch_still_fails(self):
        from app.matching.engine import Matcher
        from app.models.profile import UserProfile

        m = Matcher(CATALOGS, None)
        job = SimpleNamespace(household_provinces=["山东省"])
        p = UserProfile(name="x", education=[], household_province="广东")
        r = m._household(job, p)
        self.assertEqual(r.status, "fail")


class TestExpandToAttachmentsDir(unittest.TestCase):
    """附件必须落到 data/attachments（机器管理），不得进入 data/inbox（用户投放）——
    否则被 inbox 扫描二次解析成无截止日副本（真实联调发现过的回归）。"""

    def test_download_lands_in_attachments_not_inbox(self):
        import app.pipeline.c_extract as cx
        from app.pipeline.run_daily import collect_jobs_from_inbox

        write_workbook(
            "exp_src.xlsx", "岗位表",
            [["招聘单位", "岗位名称", "需求人数", "学历要求", "专业要求"],
             ["某局", "科员", "1", "硕士研究生", "不限"]],
        )
        payload = Path("exp_src.xlsx").read_bytes()
        html = '<a href="http://x.com/att/岗位表.xlsx">附件</a>'
        src = SimpleNamespace(id="gd-t", path=4)

        orig = cx.fetch_url
        cx.fetch_url = lambda url: payload
        try:
            root = Path(".")
            jobs, note = cx.expand_attachment_jobs(
                html, "http://x.com/post_1.html", src, CATALOGS, root,
                apply_deadline=date(2026, 9, 30),
            )
        finally:
            cx.fetch_url = orig

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].apply_deadline, date(2026, 9, 30))
        self.assertTrue((root / "data" / "attachments").glob("gd-t__*"))
        # inbox 扫描不得看到该附件
        inbox_jobs, _bad = collect_jobs_from_inbox(root / "data" / "inbox", CATALOGS)
        self.assertFalse(any(j.id.startswith("gd-t-") for j in inbox_jobs))


if __name__ == "__main__":
    unittest.main()
