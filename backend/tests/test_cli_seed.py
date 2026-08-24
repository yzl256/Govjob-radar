import shutil
import unittest
from pathlib import Path

from app.cli import _ensure_seed

ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = ROOT / "data" / "test_seed"


class TestEnsureSeed(unittest.TestCase):
    """开箱即用播种：全新环境播种样例；已有数据绝不覆盖；标记防重复。"""

    def setUp(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)
        TEST_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_fresh_env_seeds_sample(self):
        root = TEST_DIR / "fresh"
        root.mkdir()
        self.assertTrue(_ensure_seed(root))
        sample = root / "data" / "inbox" / "sample_guokao_2027.xlsx"
        self.assertTrue(sample.exists(), "应生成样例职位表")
        self.assertTrue(sample.stat().st_size > 1000, "xlsx 应非空")
        self.assertTrue((root / "data" / ".seeded").exists(), "应写播种标记")
        self.assertTrue((root / "data" / "out").exists(), "应建 out 目录")
        # 样例表可被解析器读出岗位（端到端自证）
        from app.crawler.guokao import parse_guokao_workbook
        from app.knowledge.catalogs import load_catalogs

        jobs = parse_guokao_workbook(str(sample), load_catalogs(ROOT))
        self.assertGreaterEqual(len(jobs), 5)

    def test_marker_prevents_reseed(self):
        root = TEST_DIR / "fresh"
        root.mkdir()
        _ensure_seed(root)
        (root / "data" / "inbox" / "sample_guokao_2027.xlsx").unlink()  # 用户删掉样例
        self.assertFalse(_ensure_seed(root), "标记存在时不再播种")
        self.assertFalse(any((root / "data" / "inbox").glob("*.xlsx")), "删掉的样例不应复活")

    def test_existing_inbox_not_seeded(self):
        root = TEST_DIR / "has_xlsx"
        (root / "data" / "inbox").mkdir(parents=True)
        (root / "data" / "inbox" / "real.xlsx").write_bytes(b"x")
        self.assertFalse(_ensure_seed(root), "已有职位表不播种")
        self.assertFalse((root / "data" / ".seeded").exists(), "不写标记（用户数据优先）")

    def test_existing_store_not_seeded(self):
        root = TEST_DIR / "has_store"
        root.mkdir(parents=True)
        (root / "data").mkdir()
        (root / "data" / "jobs.jsonl").write_text('{"x":1}\n', encoding="utf-8")
        self.assertFalse(_ensure_seed(root), "已有岗位库不播种")


if __name__ == "__main__":
    unittest.main()
