import unittest
from pathlib import Path

from app.scheduler.sources import load_profiles, load_sources

ROOT = Path(__file__).resolve().parents[2]


class TestLoadSources(unittest.TestCase):
    def test_national_always(self):
        specs = load_sources(ROOT, subscribed_provinces=None)
        ids = [s.id for s in specs]
        self.assertEqual(ids, [  # 只剩 national（未订阅任何省）
            "cn-guokao", "cn-jdwz", "cn-guopin", "cn-xibu",
            "cn-tegang", "cn-gaoxiaojob", "cn-chrm",
        ])

    def test_subscribed_provinces_union(self):
        specs = load_sources(ROOT, subscribed_provinces=["广东省", "浙江省"])
        ids = {s.id for s in specs}
        self.assertIn("gd-shengkao", ids)
        self.assertIn("zj-shengkao", ids)
        self.assertNotIn("sd-shengkao", ids)  # 山东未订阅
        self.assertIn("cn-guokao", ids)  # 全国源恒在

    def test_unknown_province_silent(self):
        specs = load_sources(ROOT, subscribed_provinces=["火星省"])
        self.assertTrue(all(s.province_file == "national" for s in specs))

    def test_spec_fields(self):
        specs = load_sources(ROOT, ["广东省"])
        gk = [s for s in specs if s.id == "gd-shengkao"][0]
        self.assertEqual(gk.tier, "A")
        self.assertEqual(gk.extractor, "excel")
        self.assertEqual(gk.schedule.get("daily"), 2)
        self.assertTrue(gk.is_active_source)

    def test_entry_null_not_active(self):
        specs = load_sources(ROOT, ["广东省"])
        shequ = [s for s in specs if s.id == "gd-shequ"][0]
        self.assertFalse(shequ.is_active_source)  # entry 为 null 的占位源


class TestLoadProfiles(unittest.TestCase):
    def test_load_user_profile(self):
        """用临时目录隔离：config/profiles/user.json 是用户实时编辑的文件，内容不可断言。"""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td) / "config" / "profiles"
            pdir.mkdir(parents=True)
            (pdir / "user.json").write_text(json.dumps({
                "name": "user",
                "education": [{"level": "硕士", "major_code": "085411"}],
                "subscribed_provinces": ["广东省", "山东省", "浙江省"],
            }, ensure_ascii=False), encoding="utf-8")
            (pdir / "broken.json").write_text("{oops", encoding="utf-8")  # 坏文件跳过不炸

            profiles = load_profiles(Path(td))
            names = [p.get("name") for p in profiles]
            self.assertIn("user", names)
            user = next(p for p in profiles if p["name"] == "user")
            self.assertEqual(user["education"][0]["major_code"], "085411")
            self.assertEqual(user["subscribed_provinces"], ["广东省", "山东省", "浙江省"])


if __name__ == "__main__":
    unittest.main()
