import unittest
from datetime import date

from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import load_catalogs
from app.matching.majors import build_candidates, eval_major_rules
from app.models.job import MajorPolicy, MajorRule
from app.models.profile import EducationRecord, EduLevel, UserProfile


def profile_with(edu_records, **kw):
    base = dict(birth_date=date(1998, 7, 15), gender="女")
    base.update(kw)
    return UserProfile(education=[EducationRecord(**r) for r in edu_records], **base)


CS_MASTER = dict(level=EduLevel.MASTER, major_name="计算机科学与技术", major_code="0812")
CS_PRO_MASTER = dict(level=EduLevel.MASTER, major_name="电子信息", major_code="0854")
CS_BACHELOR = dict(level=EduLevel.BACHELOR, major_name="计算机科学与技术", major_code="080901")
EE_ACADEMIC_MASTER = dict(level=EduLevel.MASTER, major_name="电子科学与技术", major_code="0809")
LAW_MASTER = dict(level=EduLevel.MASTER, major_name="法学", major_code="0301")
FIN_PRO_MASTER = dict(level=EduLevel.MASTER, major_name="金融", major_code="0251")
NO_CODE_BACHELOR = dict(level=EduLevel.BACHELOR, major_name="计算机科学与技术", major_code="")


class TestMajorMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = load_catalogs()
        cls.aliases = load_aliases()

    def eval(self, rules, candidates):
        return eval_major_rules(
            [MajorRule(**r) if isinstance(r, dict) else r for r in rules], candidates, self.aliases
        )

    def cands(self, records, lo=None, hi=None, policy=MajorPolicy.HIGHEST_ONLY):
        p = profile_with(records)
        cands, missing = build_candidates(p, lo, hi, policy, self.catalogs)
        return cands, missing

    # ── 候选构建 ──────────────────────────────────────────
    def test_candidates_highest_only(self):
        cands, _ = self.cands([CS_BACHELOR, CS_MASTER], lo=EduLevel.BACHELOR)
        self.assertEqual([c.code for c in cands], ["0812"])  # 只取最高（硕士）

    def test_candidates_any_degree(self):
        cands, _ = self.cands(
            [CS_BACHELOR, CS_MASTER], lo=EduLevel.BACHELOR, policy=MajorPolicy.ANY_DEGREE
        )
        self.assertEqual(sorted(c.code for c in cands), ["080901", "0812"])

    def test_candidates_catalog_inference(self):
        cands, _ = self.cands([CS_PRO_MASTER], lo=EduLevel.MASTER)
        self.assertEqual((cands[0].catalog, cands[0].code), ("professional", "0854"))
        cands2, _ = self.cands([CS_MASTER], lo=EduLevel.MASTER)
        self.assertEqual(cands2[0].catalog, "academic")

    def test_candidates_missing_code(self):
        cands, missing = self.cands([NO_CODE_BACHELOR], lo=EduLevel.BACHELOR)
        self.assertEqual(cands, [])
        self.assertIn("未填写专业代码", missing)

    def test_candidates_out_of_band(self):
        # 岗位限硕士及以上：本科生档案无候选 → 缺失说明
        cands, missing = self.cands([CS_BACHELOR], lo=EduLevel.MASTER)
        self.assertEqual(cands, [])
        self.assertIn("没有", missing)

    # ── 规则求值 ──────────────────────────────────────────
    def test_rule_any(self):
        v, d = self.eval([{"type": "any"}], [])
        self.assertTrue(v)

    def test_rule_exact_code(self):
        cands, _ = self.cands([CS_MASTER], lo=EduLevel.MASTER)
        v, _ = self.eval([{"type": "code", "value": ["0812", "0835"]}], cands)
        self.assertTrue(v)
        v2, _ = self.eval([{"type": "code", "value": ["0301"]}], cands)
        self.assertFalse(v2)

    def test_rule_exact_code_ignores_suffix(self):
        # 本科 080904K vs 岗位写 080904
        cands, _ = self.cands(
            [dict(level=EduLevel.BACHELOR, major_name="信息安全", major_code="080904K")],
            lo=EduLevel.BACHELOR,
        )
        v, _ = self.eval([{"type": "code", "value": ["080904"]}], cands)
        self.assertTrue(v)

    def test_rule_prefix(self):
        cands, _ = self.cands([CS_BACHELOR], lo=EduLevel.BACHELOR)
        v, _ = self.eval([{"type": "prefix", "value": "0809", "scope": "undergraduate"}], cands)
        self.assertTrue(v)  # 本科 080901 ∈ 计算机类(0809)
        v2, _ = self.eval([{"type": "prefix", "value": "0203"}], cands)
        self.assertFalse(v2)

    def test_rule_prefix_class_family_cross_catalog(self):
        # 本科"计算机类(0809)"岗 + 计算机硕士(0812)：类族映射应命中（国考口径）
        cands, _ = self.cands([CS_MASTER], lo=EduLevel.BACHELOR)  # 本科及以上→band含本科，HIGHEST_ONLY取硕士
        v, _ = self.eval([{"type": "prefix", "value": "0809", "scope": "undergraduate"}], cands)
        self.assertTrue(v)

    def test_rule_prefix_class_family_professional(self):
        # 专硕 0854 电子信息 报 本科"计算机类"岗 → 类族含 0854 → 命中
        cands, _ = self.cands([CS_PRO_MASTER], lo=EduLevel.BACHELOR)
        v, _ = self.eval([{"type": "prefix", "value": "0809", "scope": "undergraduate"}], cands)
        self.assertTrue(v)

    def test_rule_prefix_class_family_law(self):
        # 本科"法学类"岗 + 法学硕士 0301 → 命中
        cands, _ = self.cands([LAW_MASTER], lo=EduLevel.BACHELOR)
        v, _ = self.eval([{"type": "prefix", "value": "0301", "scope": "undergraduate"}], cands)
        self.assertTrue(v)

    def test_rule_prefix_family_present_miss_is_fail(self):
        # 计算机类(0809)岗 + 电子学硕(0809 学术)：类族已收录但不包含 0809 → ❌（电子≠计算机）
        ee = dict(level=EduLevel.MASTER, major_name="电子科学与技术", major_code="0809")
        cands, _ = self.cands([ee], lo=EduLevel.BACHELOR)
        v, detail = self.eval([{"type": "prefix", "value": "0809", "scope": "undergraduate"}], cands)
        self.assertFalse(v)

    def test_rule_prefix_family_absent_is_insufficient(self):
        # "金融学类(0203)"岗 + 计算机硕士：类族未收录 → ⚠️ 而非 ❌
        cands, _ = self.cands([CS_MASTER], lo=EduLevel.BACHELOR)
        v, detail = self.eval([{"type": "prefix", "value": "0203", "scope": "undergraduate"}], cands)
        self.assertIsNone(v)
        self.assertIn("未收录", detail)

    def test_rule_exact_ug_code_grad_candidate_insufficient(self):
        # 岗位只写本科6位代码 080901 + 本科及以上 + 计算机硕士 → ⚠️（需人工确认）
        cands, _ = self.cands([CS_MASTER], lo=EduLevel.BACHELOR)
        v, detail = self.eval(
            [{"type": "code", "value": "080901", "scope": "undergraduate"}], cands
        )
        self.assertIsNone(v)
        self.assertIn("人工确认", detail)

    def test_rule_exact_ug_code_bachelor_candidate_normal(self):
        # 本科生本人报本科代码岗：正常精确匹配
        cands, _ = self.cands([CS_BACHELOR], lo=EduLevel.BACHELOR)
        v, _ = self.eval([{"type": "code", "value": "080901", "scope": "undergraduate"}], cands)
        self.assertTrue(v)

    def test_rule_prefix_cross_catalog_semantics(self):
        # 岗位写"0809类"指本科计算机类；电子学硕(0809)不带 scope 时也会命中（宽松），带 scope 后不命中
        ee_cands, _ = self.cands([EE_ACADEMIC_MASTER], lo=EduLevel.MASTER)
        v_loose, _ = self.eval([{"type": "prefix", "value": "0809"}], ee_cands)
        self.assertTrue(v_loose)  # 宽松口径：学术 0809 = 电子科学与技术
        v_strict, _ = self.eval(
            [{"type": "prefix", "value": "0809", "scope": "undergraduate"}], ee_cands
        )
        self.assertFalse(v_strict)  # 严格口径：本科计算机类不含学术代码

    def test_rule_discipline_prefix(self):
        cands, _ = self.cands([CS_MASTER], lo=EduLevel.MASTER)
        v, _ = self.eval([{"type": "prefix", "value": "08"}], cands)  # 工学门类
        self.assertTrue(v)
        v2, _ = self.eval([{"type": "prefix", "value": "03"}], cands)
        self.assertFalse(v2)

    def test_rule_text_alias_hit(self):
        cands, _ = self.cands([CS_MASTER, CS_BACHELOR], lo=EduLevel.BACHELOR, policy=MajorPolicy.ANY_DEGREE)
        v, _ = self.eval([{"type": "text", "value": "计算机相关"}], cands)
        self.assertTrue(v)

    def test_rule_text_alias_professional(self):
        # 专硕 0854 电子信息 → "计算机相关" 命中
        cands, _ = self.cands([CS_PRO_MASTER], lo=EduLevel.MASTER)
        v, _ = self.eval([{"type": "text", "value": "计算机相关"}], cands)
        self.assertTrue(v)

    def test_rule_text_alias_miss(self):
        cands, _ = self.cands([LAW_MASTER], lo=EduLevel.MASTER)
        v, _ = self.eval([{"type": "text", "value": "计算机相关"}], cands)
        self.assertFalse(v)

    def test_rule_text_alias_econ(self):
        cands, _ = self.cands([FIN_PRO_MASTER], lo=EduLevel.MASTER)
        v, _ = self.eval([{"type": "text", "value": "经济金融类"}], cands)
        self.assertTrue(v)

    def test_rule_text_unknown_alias_is_insufficient(self):
        cands, _ = self.cands([CS_MASTER], lo=EduLevel.MASTER)
        v, detail = self.eval([{"type": "text", "value": "航空航天相关"}], cands)
        self.assertIsNone(v)  # 未收录别名 → ⚠️ 而非 ❌
        self.assertIn("未收录别名表", detail)

    def test_rules_or_semantics(self):
        cands, _ = self.cands([CS_MASTER], lo=EduLevel.MASTER)
        v, _ = self.eval(
            [{"type": "code", "value": "0301"}, {"type": "code", "value": ["0812"]}], cands
        )
        self.assertTrue(v)  # 第二条命中

    def test_missing_code_with_code_rule_is_insufficient(self):
        cands, missing = self.cands([NO_CODE_BACHELOR], lo=EduLevel.BACHELOR)
        v, detail = self.eval([{"type": "code", "value": "080901"}], cands)
        self.assertIsNone(v)
        self.assertIn("缺专业代码", detail)
        self.assertIn("未填写专业代码", missing)

    def test_empty_rules_means_any(self):
        v, _ = self.eval([], [])
        self.assertTrue(v)


if __name__ == "__main__":
    unittest.main()
