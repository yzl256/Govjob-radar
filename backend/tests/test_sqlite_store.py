import json
import os
import shutil
import unittest
from pathlib import Path

from app.store import db as dbstore

ROOT = Path(__file__).resolve().parents[2]
# 沙箱约定：只写仓库内 data/（系统 temp 不可写）；ignore_errors 兜底 Windows 句柄延迟释放
TEST_DIR = ROOT / "data" / "test_sqlite"


class TestSQLiteStore(unittest.TestCase):
    """SQLite 持久层：llm_config 部分更新、档案往返、脱敏。库文件用 data/test_sqlite/ 隔离。"""

    def setUp(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)
        TEST_DIR.mkdir(parents=True, exist_ok=True)
        self.db = TEST_DIR / "test.db"

    def tearDown(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_llm_config_roundtrip(self):
        self.assertIsNone(dbstore.get_llm_config(self.db))
        saved = dbstore.save_llm_config(self.db, provider="deepseek", api_key="sk-abc123def456", base_url="https://api.deepseek.com/", model="deepseek-chat")
        self.assertEqual(saved["api_key"], "sk-abc123def456")
        self.assertEqual(saved["base_url"], "https://api.deepseek.com")  # 尾斜杠剥掉
        row = dbstore.get_llm_config(self.db)
        self.assertEqual(row["api_key"], "sk-abc123def456")
        self.assertEqual(row["model"], "deepseek-chat")
        self.assertEqual(row["provider"], "deepseek")
        self.assertTrue(row["updated_at"])

    def test_llm_config_partial_update_keeps_old(self):
        """前端"Key 留空不改"语义：None 字段保留原值。"""
        dbstore.save_llm_config(self.db, provider="deepseek", api_key="sk-keep-me-000", base_url="https://api.deepseek.com", model="deepseek-chat")
        saved = dbstore.save_llm_config(self.db, api_key=None, base_url=None, model="deepseek-reasoner")
        self.assertEqual(saved["api_key"], "sk-keep-me-000")  # 未传 → 保留
        self.assertEqual(saved["model"], "deepseek-reasoner")
        self.assertEqual(saved["provider"], "deepseek")

    def test_migration_adds_provider_column(self):
        """旧库（无 provider 列）→ connect 时补列，已有 key 回填 deepseek。"""
        import sqlite3

        TEST_DIR.mkdir(parents=True, exist_ok=True)
        old = TEST_DIR / "old_schema.db"
        conn = sqlite3.connect(str(old))
        conn.execute(
            "CREATE TABLE llm_config (id INTEGER PRIMARY KEY CHECK (id=1), api_key TEXT NOT NULL DEFAULT '',"
            " base_url TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO llm_config (id, api_key, base_url, model, updated_at) VALUES (1, 'sk-old-key-123', 'https://api.deepseek.com', 'deepseek-chat', '2025-01-01')"
        )
        conn.commit()
        conn.close()
        # 触发迁移（get_llm_config → connect → _migrate）
        row = dbstore.get_llm_config(old)
        self.assertEqual(row["provider"], "deepseek")  # 回填
        self.assertEqual(row["api_key"], "sk-old-key-123")  # 原数据不动

    def test_mask_key(self):
        self.assertEqual(dbstore.mask_key("sk-abc123def456"), "sk-a****f456")
        self.assertEqual(dbstore.mask_key("short"), "sh****")
        self.assertEqual(dbstore.mask_key(""), "")
        self.assertEqual(dbstore.mask_key(None), "")

    def test_profile_roundtrip(self):
        self.assertIsNone(dbstore.get_profile(self.db))
        profile = {"name": "张三", "education": [{"level": "硕士", "major_code": "085411"}], "subscribed_provinces": ["广东省"]}
        dbstore.save_profile(self.db, profile)
        loaded = dbstore.get_profile(self.db)
        self.assertEqual(loaded["name"], "张三")
        self.assertEqual(loaded["education"][0]["major_code"], "085411")
        # 覆盖保存
        dbstore.save_profile(self.db, {"name": "李四", "education": []})
        self.assertEqual(dbstore.get_profile(self.db)["name"], "李四")

    def test_repeated_connect_idempotent(self):
        """多次连接（建表幂等）+ 并发短连接不互斥。"""
        dbstore.save_llm_config(self.db, api_key="sk-x")
        dbstore.save_llm_config(self.db, model="m")  # 第二次连接再建表不报错
        self.assertEqual(dbstore.get_llm_config(self.db)["api_key"], "sk-x")


class TestLLMConfigChain(unittest.TestCase):
    """client.llm_config 配置链：SQLite（root）优先，环境变量兜底。
    db 路径平铺在 TEST_DIR 下（沙箱只放行已存在目录的直接子项，
    运行时多层新建目录里的 SQLite 文件打不开——见 app/store/jobs.py 同款约定）。"""

    DB = TEST_DIR / "chain.db"

    def setUp(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)
        TEST_DIR.mkdir(parents=True, exist_ok=True)
        self._env = {k: os.environ.pop(k) for k in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL") if k in os.environ}

    def tearDown(self):
        os.environ.update(self._env)
        shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_db_overrides_env(self):
        from app.llm.client import HttpLLM, llm_available, llm_config

        dbstore.save_llm_config(self.DB, api_key="sk-from-db-999", base_url="https://db.example.com", model="db-model")
        os.environ["DEEPSEEK_API_KEY"] = "sk-from-env"
        cfg = llm_config(db=self.DB)
        self.assertEqual(cfg["api_key"], "sk-from-db-999")  # DB 优先
        self.assertEqual(cfg["base_url"], "https://db.example.com")
        self.assertEqual(cfg["model"], "db-model")
        self.assertTrue(llm_available(db=self.DB))
        llm = HttpLLM(db=self.DB)
        self.assertEqual(llm.api_key, "sk-from-db-999")

    def test_env_fallback_when_db_empty(self):
        from app.llm.client import llm_config

        os.environ["DEEPSEEK_API_KEY"] = "sk-from-env"
        cfg = llm_config(db=self.DB)  # 库里无记录 → env 兜底
        self.assertEqual(cfg["api_key"], "sk-from-env")
        self.assertEqual(cfg["model"], "deepseek-chat")  # 默认

    def test_db_base_url_with_env_key(self):
        """DB 存了 base_url/model 但没 key：key 用 env，其余用 DB 值。"""
        from app.llm.client import llm_config

        dbstore.save_llm_config(self.DB, api_key="", base_url="https://db.example.com", model="db-model")
        os.environ["LLM_API_KEY"] = "sk-from-env"
        cfg = llm_config(db=self.DB)
        self.assertEqual(cfg["api_key"], "sk-from-env")
        self.assertEqual(cfg["base_url"], "https://api.deepseek.com")  # DB 无 key 整条不采信
        self.assertEqual(cfg["model"], "deepseek-chat")

    def test_no_root_pure_env(self):
        from app.llm.client import llm_config

        os.environ["DEEPSEEK_API_KEY"] = "sk-env-only"
        cfg = llm_config()  # 不传 root/db：绝不读库（CLI/测试旧行为）
        self.assertEqual(cfg["api_key"], "sk-env-only")

    def test_corrupt_db_not_fatal(self):
        from app.llm.client import llm_config

        bad = TEST_DIR / "corrupt.db"  # 平铺路径；坏库文件在沙箱下可能删不干净，独立命名避免污染
        bad.write_text("not a sqlite file", encoding="utf-8")
        os.environ["DEEPSEEK_API_KEY"] = "sk-env-ok"
        cfg = llm_config(db=bad)  # 坏库吞掉 → env 兜底
        self.assertEqual(cfg["api_key"], "sk-env-ok")


if __name__ == "__main__":
    unittest.main()
