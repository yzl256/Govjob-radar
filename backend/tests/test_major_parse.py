import unittest

from app.knowledge.catalogs import load_catalogs
from app.knowledge.major_parse import parse_major_cell
from app.models.job import MajorRuleType


class TestMajorParse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = load_catalogs()

    def types(self, cell):
        return [r.type for r in parse_major_cell(cell, self.catalogs)]

    def test_empty_or_buxian(self):
        self.assertEqual(self.types(""), [MajorRuleType.ANY])
        self.assertEqual(self.types("不限"), [MajorRuleType.ANY])
        self.assertEqual(self.types("专业不限"), [MajorRuleType.ANY])
        self.assertEqual(self.types(" 无要求 "), [MajorRuleType.ANY])

    def test_code_prefixed(self):
        rules = parse_major_cell("0812计算机科学与技术、0835软件工程", self.catalogs)
        codes = {r.value for r in rules if r.type == MajorRuleType.CODE}
        self.assertEqual(codes, {"0812", "0835"})
        self.assertTrue(all(r.scope is None for r in rules))  # 4位码不限目录

    def test_six_digit_code_gets_undergrad_scope(self):
        rules = parse_major_cell("080901计算机科学与技术", self.catalogs)
        r = rules[0]
        self.assertEqual((r.type, r.value, r.scope), (MajorRuleType.CODE, "080901", "undergraduate"))

    def test_class_name_with_paren_code(self):
        # "计算机类（0809）"：类名命中本科目录 → prefix；括号码也并入 code
        rules = parse_major_cell("计算机类（0809）", self.catalogs)
        prefixes = [r for r in rules if r.type == MajorRuleType.PREFIX]
        codes = [r for r in rules if r.type == MajorRuleType.CODE]
        self.assertEqual(prefixes[0].value, "0809")
        self.assertEqual(prefixes[0].scope, "undergraduate")
        self.assertEqual(codes[0].value, "0809")

    def test_class_name_only(self):
        rules = parse_major_cell("工商管理类", self.catalogs)
        r = rules[0]
        self.assertEqual(r.type, MajorRuleType.PREFIX)
        self.assertEqual(r.scope, "undergraduate")
        self.assertEqual(r.value, "1202")

    def test_pure_name_multi_catalog_hits(self):
        # "法学" 同时命中 本科030101K 与 学术0301 → 两条 CODE 规则 OR
        rules = parse_major_cell("法学", self.catalogs)
        code_rules = {(r.value, r.scope) for r in rules if r.type == MajorRuleType.CODE}
        self.assertIn(("0301", "academic"), code_rules)
        self.assertIn(("030101", "undergraduate"), code_rules)

    def test_pure_name_professional_hit(self):
        rules = parse_major_cell("翻译", self.catalogs)
        code_rules = {(r.value, r.scope) for r in rules if r.type == MajorRuleType.CODE}
        self.assertIn(("0551", "professional"), code_rules)

    def test_unknown_name_becomes_text(self):
        rules = parse_major_cell("消防工程", self.catalogs)  # 不在种子目录
        r = rules[0]
        self.assertEqual((r.type, r.value), (MajorRuleType.TEXT, "消防工程"))

    def test_fuzzy_phrase_becomes_text(self):
        rules = parse_major_cell("计算机相关", self.catalogs)
        self.assertEqual(rules[0].type, MajorRuleType.TEXT)

    def test_mixed_names(self):
        rules = parse_major_cell("统计学、应用统计学", self.catalogs)
        codes = {r.value for r in rules if r.type == MajorRuleType.CODE}
        self.assertIn("071201", codes)  # 本科 统计学
        self.assertIn("071202", codes)  # 本科 应用统计学
        self.assertIn("0714", codes)  # 学术 统计学(一级学科) 同名命中

    def test_paren_grouped_codes_only(self):
        rules = parse_major_cell("法律类（学科代码：0301、0302）", self.catalogs)
        codes = {r.value for r in rules if r.type == MajorRuleType.CODE}
        self.assertTrue({"0301", "0302"} <= codes)


if __name__ == "__main__":
    unittest.main()
