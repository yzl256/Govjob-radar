import json
import unittest
from datetime import date
from pathlib import Path

from app.knowledge.catalogs import load_catalogs
from app.llm.client import FakeLLM, parse_json_loose
from app.llm.extract import extract_jobs_from_text, normalize_llm_jobs
from app.llm.htmltext import html_to_text
from app.models.job import MajorRuleType
from app.pipeline.c_extract import discover_announcement_links
from app.store.jobs import append_jobs, load_jobs

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = load_catalogs(ROOT)

GOOD_RESPONSE = {
    "is_job_announcement": True,
    "jobs": [
        {
            "title": "大数据管理局直属事业单位公开招聘工作人员",
            "employer": "深圳市大数据资源管理中心",
            "region": "广东省深圳市",
            "quota": 3,
            "edu_min": "硕士",
            "majors": ["计算机类", "0854 电子信息", "大数据技术与工程"],
            "birth_after": "1998-07-01",
            "age_max": 35,
            "gender_limit": None,
            "political_req": "中共党员（含预备党员）",
            "fresh_only": True,
            "household_provinces": [],
            "require_double_first_class": True,
            "apply_deadline": "2026年9月10日",
            "publish_date": "2026-08-20",
            "evidence": {"title": "公开招聘工作人员公告", "edu": "硕士研究生及以上学历", "major": "计算机类、电子信息类等相关专业"},
        }
    ],
}


class TestHtmlText(unittest.TestCase):
    def test_strips_script_keeps_anchor(self):
        html = (
            "<html><head><script>var x=1;</script><style>.a{}</style></head>"
            "<body><div>导航</div>"
            '<a href="/2026/t123.shtml">《2026年深圳市某区事业单位公开招聘公告》</a>'
            "<p>报名时间：2026年9月1日至9月10日</p></body></html>"
        )
        text = html_to_text(html)
        self.assertNotIn("var x=1", text)
        self.assertNotIn(".a{}", text)
        self.assertIn("《2026年深圳市某区事业单位公开招聘公告》", text)  # 锚文本定界
        self.assertIn("报名时间", text)

    def test_truncates_long_text(self):
        text = html_to_text("<p>" + "字" * 20000 + "</p>", max_chars=1000)
        self.assertLessEqual(len(text), 1020)
        self.assertIn("超长截断", text)


class TestParseJsonLoose(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_json_loose('{"a": 1}'), {"a": 1})

    def test_markdown_fenced(self):
        self.assertEqual(parse_json_loose('```json\n{"a": [1,2]}\n```'), {"a": [1, 2]})

    def test_json_in_prose(self):
        self.assertEqual(parse_json_loose('结果是：{"ok": true} 请核对'), {"ok": True})


class TestNormalize(unittest.TestCase):
    def test_good_response_full_chain(self):
        jobs = normalize_llm_jobs(GOOD_RESPONSE, "gd-sydw", 4, CATALOGS, source_url="https://x.gov.cn/a")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertTrue(j.id.startswith("gd-sydw-"))
        self.assertEqual(j.path, 4)  # 源口径优先，LLM 不决定 path
        self.assertEqual(j.title[:6], "大数据管理局")
        self.assertEqual(j.region_province, "广东省")
        self.assertEqual(j.edu_require.min_level.value, "硕士")
        self.assertEqual(j.birth_after, date(1998, 7, 1))
        self.assertEqual(j.age_max, 35)
        self.assertEqual(j.apply_deadline, date(2026, 9, 10))  # 中文日期转换
        self.assertTrue(j.fresh_only)
        self.assertTrue(j.require_double_first_class)
        self.assertIsNone(j.household_provinces)  # 空列表 → 不限
        self.assertEqual(
            [s.value for s in j.political_req], ["党员", "预备党员"]
        )
        # 专业原文 → 本地确定性解析
        types = {r.type for r in j.major_rules}
        self.assertIn(MajorRuleType.PREFIX, types)  # 计算机类
        self.assertIn(MajorRuleType.CODE, types)  # 0854
        self.assertIn("证据", j.other_notes)

    def test_not_announcement(self):
        self.assertEqual(normalize_llm_jobs({"is_job_announcement": False, "jobs": []}, "x", 4, CATALOGS), [])

    def test_null_fields_stay_unknown(self):
        resp = {"is_job_announcement": True, "jobs": [{"title": "某岗", "majors": ["不限"]}]}
        j = normalize_llm_jobs(resp, "x", 5, CATALOGS)[0]
        self.assertIsNone(j.edu_require.min_level)
        self.assertIsNone(j.birth_after)
        self.assertIsNone(j.apply_deadline)
        self.assertEqual(j.major_rules[0].type, MajorRuleType.ANY)

    def test_bad_row_skipped_not_fatal(self):
        resp = {"is_job_announcement": True, "jobs": [{"title": ""}, {"no_title": 1}, GOOD_RESPONSE["jobs"][0]]}
        jobs = normalize_llm_jobs(resp, "x", 4, CATALOGS)
        self.assertEqual(len(jobs), 1)  # 坏行丢弃，好行保留

    def test_stable_id_dedupe_basis(self):
        a = normalize_llm_jobs(GOOD_RESPONSE, "gd-sydw", 4, CATALOGS, source_url="https://x.gov.cn/a")[0]
        b = normalize_llm_jobs(GOOD_RESPONSE, "gd-sydw", 4, CATALOGS, source_url="https://x.gov.cn/a")[0]
        self.assertEqual(a.id, b.id)


class TestStore(unittest.TestCase):
    """写入 data/ 下带唯一后缀的临时文件（沙箱不允许运行时新建目录内的写入）。"""

    def setUp(self):
        import os

        self.path = ROOT / "data" / f"test_jobs_{os.getpid()}_{id(self)}.jsonl"

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()

    def test_append_and_dedupe_and_load(self):
        jobs = normalize_llm_jobs(GOOD_RESPONSE, "t-src", 4, CATALOGS, source_url="https://x/a")
        added, skipped = append_jobs(ROOT, jobs, path=self.path)
        self.assertEqual((added, skipped), (1, 0))
        added, skipped = append_jobs(ROOT, jobs, path=self.path)  # 同 id 再入 → 跳过
        self.assertEqual((added, skipped), (0, 1))
        loaded = load_jobs(ROOT, path=self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].title, jobs[0].title)

    def test_load_bad_lines_tolerated(self):
        j = normalize_llm_jobs(GOOD_RESPONSE, "t", 4, CATALOGS)[0]
        self.path.write_text("不是json\n\n" + j.model_dump_json() + "\n", encoding="utf-8")
        self.assertEqual(len(load_jobs(ROOT, path=self.path)), 1)


class TestDiscoverLinks(unittest.TestCase):
    def test_finds_recruit_links_only(self):
        html = (
            '<a href="/a.shtml">2026年区属事业单位公开招聘公告</a>'
            '<a href="/b.htm">政府工作报告</a>'
            '<a href="/c.shtml"><span>关于开展人才引进的通告</span></a>'
            '<a href="https://other.gov.cn/d.aspx">三支一扶招募公告</a>'
        )
        links = discover_announcement_links("https://x.gov.cn/list/", html)
        self.assertEqual(links[0], "https://x.gov.cn/a.shtml")
        self.assertIn("https://x.gov.cn/c.shtml", links)
        self.assertIn("https://other.gov.cn/d.aspx", links)
        self.assertNotIn("https://x.gov.cn/b.htm", links)

    def test_limit(self):
        html = "".join(f'<a href="/{i}.shtml">招聘公告{i}</a>' for i in range(20))
        self.assertEqual(len(discover_announcement_links("https://x.gov.cn/", html, limit=8)), 8)


class TestExtractFromText(unittest.TestCase):
    def test_fake_llm_pipeline(self):
        llm = FakeLLM(GOOD_RESPONSE)
        text = "（某公告正文，内容不重要——FakeLLM 回放预制结果）"
        jobs = extract_jobs_from_text(text, llm, "gd-sydw", 4, CATALOGS, source_url="https://x/a")
        self.assertEqual(len(jobs), 1)
        sys_prompt, user_prompt = llm.calls[0]
        self.assertIn("禁止编造", sys_prompt)
        self.assertIn("原文片段逐条照抄", sys_prompt)
        self.assertIn("gd-sydw", user_prompt)

    def test_expired_deadline_gets_attention(self):
        """截止日已过：判定照常但 attention 标注存档（真实联调发现的老公告场景）。"""
        from app.knowledge.alias import load_aliases
        from app.matching.engine import Matcher
        from app.models.profile import UserProfile

        j = normalize_llm_jobs(GOOD_RESPONSE, "gd-sydw", 4, CATALOGS)[0]
        j = j.model_copy(update={"apply_deadline": date(2026, 1, 1)})
        m = Matcher(CATALOGS, load_aliases(ROOT))
        r = m.match(j, UserProfile(name="x", education=[]), today=date(2026, 8, 23))
        self.assertTrue(any("截止" in a and "存档" in a for a in r.attention))
        r2 = m.match(
            j.model_copy(update={"apply_deadline": date(2026, 12, 1)}),
            UserProfile(name="x", education=[]),
            today=date(2026, 8, 23),
        )
        self.assertFalse(any("已截止" in a for a in r2.attention))


if __name__ == "__main__":
    unittest.main()
