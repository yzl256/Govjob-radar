import unittest
from pathlib import Path

from app.io.miniyaml import load_yaml_file, parse

ROOT = Path(__file__).resolve().parents[2]


class TestMiniYamlScalars(unittest.TestCase):
    def test_scalars(self):
        data = parse(
            "a: 1\nb: 1.5\nc: true\nd: null\ne: 文本\n"
            "f: '引号 # 内注释'\ng: \"双引\"\nh: [甲, 乙]\n"
            "i: {daily: 2, peak: hourly}\nj:\n"
        )
        self.assertEqual(data["a"], 1)
        self.assertEqual(data["b"], 1.5)
        self.assertIs(data["c"], True)
        self.assertIs(data["d"], None)
        self.assertEqual(data["e"], "文本")
        self.assertEqual(data["f"], "引号 # 内注释")
        self.assertEqual(data["h"], ["甲", "乙"])
        self.assertEqual(data["i"], {"daily": 2, "peak": "hourly"})
        self.assertIsNone(data["j"])

    def test_trailing_comment(self):
        data = parse("entry: https://x.gov.cn/  # TODO 普查\nk: v")
        self.assertEqual(data["entry"], "https://x.gov.cn/")
        self.assertEqual(data["k"], "v")

    def test_nested_and_seq(self):
        data = parse(
            "meta:\n  province: 广东省\n  adcode: \"440000\"\n"
            "items:\n  - id: a\n    name: 甲\n    sub:\n      - s1\n      - s2\n"
            "  - id: b\n    flag: true\nplain:\n  - x\n  - y\n"
        )
        self.assertEqual(data["meta"]["province"], "广东省")
        self.assertEqual(data["meta"]["adcode"], "440000")  # 引号防数字化
        self.assertEqual(data["items"][0]["id"], "a")
        self.assertEqual(data["items"][0]["sub"], ["s1", "s2"])
        self.assertIs(data["items"][1]["flag"], True)
        self.assertEqual(data["plain"], ["x", "y"])

    def test_block_scalar_folded(self):
        data = parse("notes: >-\n  第一行\n  第二行\nnext: ok")
        self.assertEqual(data["notes"], "第一行 第二行")
        self.assertEqual(data["next"], "ok")


class TestRealSourceFiles(unittest.TestCase):
    """用真实源站注册表回归——保证解析器覆盖项目全部 YAML 用法。"""

    @classmethod
    def setUpClass(cls):
        cls.national = load_yaml_file(ROOT / "config" / "sources" / "national.yaml")
        cls.gd = load_yaml_file(ROOT / "config" / "sources" / "广东省.yaml")
        cls.sd = load_yaml_file(ROOT / "config" / "sources" / "山东省.yaml")
        cls.zj = load_yaml_file(ROOT / "config" / "sources" / "浙江省.yaml")

    def test_counts(self):
        self.assertEqual(len(self.national["sources"]), 7)
        self.assertEqual(len(self.gd["sources"]), 13)  # 2026-08-23 新增国企源：省/深/佛/莞国资委 + 国聘网(缓)
        self.assertEqual(len(self.sd["sources"]), 7)
        self.assertEqual(len(self.zj["sources"]), 8)

    def test_national_fields(self):
        gk = self.national["sources"][0]
        self.assertEqual(gk["id"], "cn-guokao")
        self.assertEqual(gk["path"], 2)
        self.assertEqual(gk["tier"], "A")
        self.assertEqual(gk["extractor"], "excel")
        self.assertEqual(gk["schedule"], {"daily": 3, "peak": "hourly"})
        self.assertIn("职位表", gk["season"]["note"])  # season.note 普通标量
        self.assertIn("A 类解析器", gk["notes"])  # notes 折叠块标量

    def test_gd_schedule_and_entry(self):
        x = self.gd["sources"][1]  # gd-shengkao
        self.assertEqual(x["apply_entry"], "https://ggfw.hrss.gd.gov.cn/gwyks/")
        self.assertEqual(x["schedule"]["peak"], "hourly")
        # 2026-08-23 普查后升级为 surveyed（入口核实+公告验证）
        self.assertEqual(x["status"], "surveyed")

    def test_zj_inline_list_region(self):
        hz = [s for s in self.zj["sources"] if s["id"] == "zj-rcyj-hz"][0]
        self.assertEqual(hz["region"], ["浙江省", "杭州市"])
        fb = [s for s in self.zj["sources"] if s["id"] == "zj-xuandiao"][0]
        self.assertEqual(len(fb["fallback_entries"]), 2)

    def test_sd_mirror(self):
        szyf = [s for s in self.sd["sources"] if s["id"] == "sd-szyf"][0]
        self.assertEqual(szyf["mirror_entry"], "https://www.sdgxbys.cn/")


if __name__ == "__main__":
    unittest.main()
