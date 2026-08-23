import unittest

from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import (
    CATALOG_ACADEMIC,
    CATALOG_PROFESSIONAL,
    CATALOG_UNDERGRAD,
    infer_catalog,
    load_catalogs,
)


class TestCatalogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = load_catalogs()

    def test_all_catalogs_loaded(self):
        for name in (CATALOG_UNDERGRAD, CATALOG_ACADEMIC, CATALOG_PROFESSIONAL):
            self.assertGreater(len(self.catalogs[name]), 10, f"{name} 目录条目过少")

    def test_cross_catalog_code_collision_0809(self):
        """同一前缀 0809 在本科/学术目录中含义不同——知识库最核心的陷阱。"""
        u = self.catalogs[CATALOG_UNDERGRAD]
        a = self.catalogs[CATALOG_ACADEMIC]
        self.assertEqual(u.class_nodes["0809"], "计算机类")
        self.assertEqual(a.majors["0809"], "电子科学与技术")

    def test_known_codes(self):
        u = self.catalogs[CATALOG_UNDERGRAD]
        self.assertEqual(u.majors["080901"], "计算机科学与技术")
        self.assertEqual(u.majors["080904"], "信息安全")  # 080904K 归一化后
        a = self.catalogs[CATALOG_ACADEMIC]
        self.assertEqual(a.majors["0812"], "计算机科学与技术")
        self.assertEqual(a.majors["0835"], "软件工程")
        p = self.catalogs[CATALOG_PROFESSIONAL]
        self.assertEqual(p.majors["0854"], "电子信息")

    def test_infer_catalog(self):
        self.assertEqual(infer_catalog("本科", "080901", self.catalogs), CATALOG_UNDERGRAD)
        self.assertEqual(infer_catalog("硕士", "0812", self.catalogs), CATALOG_ACADEMIC)
        self.assertEqual(infer_catalog("硕士", "0854", self.catalogs), CATALOG_PROFESSIONAL)
        self.assertEqual(infer_catalog("博士", "0812", self.catalogs), CATALOG_ACADEMIC)
        self.assertIsNone(infer_catalog("大专", "510201", self.catalogs))  # 专科目录未建

    def test_alias_table(self):
        aliases = load_aliases()
        exp = aliases.get("计算机相关")
        self.assertIsNotNone(exp)
        self.assertIn("0809", exp[CATALOG_UNDERGRAD]["prefixes"])
        self.assertIn("0812", exp[CATALOG_ACADEMIC]["codes"])
        self.assertIn("0854", exp[CATALOG_PROFESSIONAL]["codes"])
        self.assertIsNone(aliases.get("航空宇航相关专业"))


if __name__ == "__main__":
    unittest.main()
