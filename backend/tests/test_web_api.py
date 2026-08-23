import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app.web.server import Handler, _State

ROOT = Path(__file__).resolve().parents[2]


class TestWebAPI(unittest.TestCase):
    """对真实仓库起临时端口跑 API。可写点为 data/web_test_profile.json 与
    data/web_test_govjob.db（data/ 目录预先存在，沙箱允许直写；测试后清理）。"""

    @classmethod
    def setUpClass(cls):
        cls.profile_file = ROOT / "data" / "web_test_profile.json"
        cls.db_file = ROOT / "data" / "web_test_govjob.db"
        for f in (cls.profile_file, cls.db_file):
            try:
                f.unlink()
            except OSError:
                pass
        Handler.state = _State(ROOT, profile_file=cls.profile_file, db_path=cls.db_file)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for f in (
            cls.profile_file,
            cls.db_file,
            cls.db_file.with_name(cls.db_file.name + "-wal"),
            cls.db_file.with_name(cls.db_file.name + "-shm"),
        ):
            try:
                f.unlink()
            except OSError:
                pass

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def _get_text(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return r.status, r.read().decode()

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_index_served(self):
        status, body = self._get_text("/")
        self.assertEqual(status, 200)
        self.assertIn("岗位雷达", body)

    def test_get_profile(self):
        status, data = self._get("/api/profile")
        self.assertEqual(status, 200)
        self.assertIn("education", data)

    def test_get_meta_provinces(self):
        status, data = self._get("/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(set(data["provinces"]), {"广东省", "山东省", "浙江省"})
        self.assertEqual(data["paths"]["2"], "国考")

    def test_dfc_endpoint(self):
        status, data = self._get("/api/dfc?school=" + __import__("urllib.parse", fromlist=["quote"]).quote("华南理工"))
        self.assertEqual(status, 200)
        self.assertTrue(data["double_first_class"])
        self.assertEqual(data["matched"], "华南理工大学")
        status, data = self._get("/api/dfc?school=" + __import__("urllib.parse", fromlist=["quote"]).quote("浙江大学城市学院"))
        self.assertFalse(data["double_first_class"])
        self.assertIsNone(data["matched"])
        try:
            self._get("/api/dfc")
            self.fail("应返回 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_llm_save_and_masked_readback(self):
        """保存 → 只回传脱敏 key，明文绝不离开服务端。"""
        status, data = self._post("/api/llm", {"api_key": "sk-webtest-abcdef1234", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved"]["api_key_masked"], "sk-w****1234")
        self.assertNotIn("sk-webtest-abcdef1234", json.dumps(data))

        status, view = self._get("/api/llm")
        self.assertEqual(status, 200)
        self.assertTrue(view["configured"])
        self.assertEqual(view["api_key_masked"], "sk-w****1234")
        self.assertEqual(view["model"], "deepseek-chat")

    def test_llm_empty_key_keeps_saved(self):
        """Key 留空只改模型 → 旧 Key 保留（前端"留空不修改"）。"""
        self._post("/api/llm", {"api_key": "sk-keep-987654321", "model": "deepseek-chat"})
        status, data = self._post("/api/llm", {"api_key": "", "model": "deepseek-reasoner"})
        self.assertEqual(status, 200)
        status, view = self._get("/api/llm")
        self.assertEqual(view["model"], "deepseek-reasoner")
        self.assertEqual(view["api_key_masked"], "sk-k****4321")  # 旧 key 未被清空

    def test_llm_connection_test_bad_base_url(self):
        """连通测试打到不可达地址 → ok=False（不发真实外网请求）。"""
        self._post("/api/llm", {"api_key": "sk-webtest-abcdef1234", "base_url": "http://127.0.0.1:9"})
        status, result = self._post("/api/llm/test", {})
        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertTrue(result["detail"])

    def test_profile_persisted_across_state_reload(self):
        """档案从 SQLite 重新加载（服务重启模拟）：删掉 JSON 镜像也能恢复。"""
        self._post("/api/profile", {"name": "persist-check", "education": []})
        try:
            self.profile_file.unlink()  # 镜像删除 → 只能靠 SQLite
        except OSError:
            pass
        Handler.state = _State(ROOT, profile_file=self.profile_file, db_path=self.db_file)  # 重建 state
        status, data = self._get("/api/profile")
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "persist-check")

    def test_post_invalid_profile_400(self):
        status, data = self._post("/api/profile", {"education": "不是列表"})
        self.assertEqual(status, 400)
        self.assertIn("无效", data["error"])

    def test_post_valid_profile_and_match(self):
        payload = {
            "name": "webtest",
            "gender": "男",
            "subscribed_provinces": [],
            "education": [
                {
                    "level": "硕士",
                    "major_name": "大数据技术与工程",
                    "major_code": "085411",
                    "catalog": "professional",
                }
            ],
        }
        status, _ = self._post("/api/profile", payload)
        self.assertEqual(status, 200)
        # 权威源：SQLite
        from app.store.db import get_profile

        db_profile = get_profile(self.db_file)
        self.assertEqual(db_profile["education"][0]["major_code"], "085411")
        # 镜像：JSON 文件（CLI / daily 流水线兼容）
        saved = json.loads(self.profile_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["education"][0]["major_code"], "085411")

        status, data = self._post("/api/match", {})
        self.assertEqual(status, 200)
        c = data["counts"]
        self.assertGreaterEqual(c["jobs_total"], 5)  # 样例职位表至少 5 岗
        self.assertGreaterEqual(c["eligible"], 1)  # 深圳计算机类岗可报
        self.assertIn("archived_expired", c)  # 已截止存档数（岗位库广东选调/三支一扶已过期）
        self.assertNotIn("prep_reference", data)  # 已截止岗位不做参考展示（仅存档计数）
        # 赛道周期看板
        board = data["track_board"]
        self.assertEqual(len(board), 10)
        self.assertEqual([b["path"] for b in board], list(range(1, 11)))
        for b in board:
            self.assertIn(b["phase"], ("open", "prep"))
        for item in data["eligible"] + data["insufficient"]:
            self.assertIn(item["verdict"], ("可报", "信息不足"))
            self.assertTrue(item["reasons"])
            self.assertIn("path_name", item)  # H5 渲染需要
            self.assertIn(item["path_name"], ("国考", "省考", "选调生", "事业单位", "人才引进", "国企央企", "军队文职", "三支一扶", "特岗/西部计划", "辅导员/社区工作者"))
            # 有效期约束：展示的岗位截止日必须 ≥ 今日（无截止日不判过期）
            if item["deadline"]:
                self.assertGreaterEqual(
                    item["deadline"],
                    __import__("datetime").date.today().isoformat(),
                    f"过期岗位泄漏到结果: {item['title']}",
                )


if __name__ == "__main__":
    unittest.main()
