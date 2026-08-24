import unittest

from app.llm.providers import PROVIDERS, provider_view


class TestProviders(unittest.TestCase):
    """供应商注册表：Base URL 的唯一权威源；对外视图不含 base_url。"""

    def test_registry_nonempty_and_complete(self):
        self.assertGreaterEqual(len(PROVIDERS), 5)
        for pid, p in PROVIDERS.items():
            self.assertTrue(p["name"])
            self.assertTrue(p["base_url"].startswith("https://"), f"{pid} base_url 应为 https")
            self.assertTrue(p["models"], f"{pid} 应至少有一个模型")
            self.assertTrue(p["key_url"].startswith("https://"))

    def test_provider_view_hides_base_url(self):
        view = provider_view("deepseek")
        self.assertEqual(view["id"], "deepseek")
        self.assertIn("models", view)
        self.assertIn("key_url", view)
        self.assertNotIn("base_url", view)  # 后端封装，前端无感

    def test_provider_view_unknown_none(self):
        self.assertIsNone(provider_view("no-such"))
