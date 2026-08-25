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
        # 全部省级行政区均可订阅（自主选省份），不限已注册源站的省份
        self.assertIn("广东省", data["provinces"])
        self.assertIn("新疆维吾尔自治区", data["provinces"])
        self.assertEqual(len(data["provinces"]), len(set(data["provinces"])))
        self.assertEqual(set(data["sources_ready"]), {"广东省", "山东省", "浙江省"})
        self.assertEqual(data["paths"]["2"], "国考")

    def test_no_store_cache_headers(self):
        """页面无缓存头：版本迭代后浏览器不残留旧版前端。"""
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as r:
            self.assertEqual(r.headers.get("Cache-Control"), "no-store")

    def test_match_province_counts(self):
        """在招岗位按省份分布：订阅省 0 岗时前端明示原因所依赖的字段。"""
        status, data = self._post("/api/match", {})
        self.assertEqual(status, 200)
        self.assertIn("province_counts", data)
        self.assertIn("sample_province_counts", data)
        self.assertIn("store_total", data)
        self.assertIsInstance(data["province_counts"], dict)
        self.assertIsInstance(data["sample_province_counts"], dict)
        for prov, n in data["province_counts"].items():
            self.assertIsInstance(n, int)
            self.assertGreater(n, 0)

    def test_sync_status_starts_idle(self):
        status, data = self._get("/api/sync/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "idle")
        self.assertEqual(data["sources"], [])

    def test_sync_uses_saved_subscription_and_invalidates_job_cache(self):
        """立即匹配前的同步必须只按当前档案已订阅省份执行，并使随后匹配重读岗位库。"""
        import app.pipeline.run_daily as daily

        self._post("/api/profile", {"name": "sync-test", "subscribed_provinces": ["浙江省"], "education": []})
        called = []
        original = daily.sync_subscribed_sources
        daily.sync_subscribed_sources = lambda root, provinces: called.append((root, provinces)) or [
            {"source_id": "zj-shengkao", "ok": True, "detail": "发现 1 个附件链接", "fetched_items": 1}
        ]
        try:
            Handler.state._jobs_cache = object()
            status, data = self._post("/api/sync", {})
        finally:
            daily.sync_subscribed_sources = original

        self.assertEqual(status, 200)
        self.assertEqual(called[0][1], ["浙江省"])
        self.assertEqual(data["provinces"], ["浙江省"])
        self.assertEqual(data["sources"][0]["source_id"], "zj-shengkao")
        self.assertIsNone(Handler.state._jobs_cache)

    def test_background_sync_reports_source_progress(self):
        """新 H5 同步接口应立即接单，并把每个完成来源回传给轮询端。"""
        import time
        import app.pipeline.run_daily as daily

        self._post("/api/profile", {"name": "sync-progress", "subscribed_provinces": ["浙江省"], "education": []})
        original = daily.sync_subscribed_sources

        def fake_sync(root, provinces, llm=None, on_progress=None):
            row = {"source_id": "zj-shengkao", "ok": True, "detail": "发现 1 个附件链接", "fetched_items": 1}
            on_progress and on_progress(row, 1, 1)
            return [row]

        daily.sync_subscribed_sources = fake_sync
        try:
            status, data = self._post("/api/sync/start", {})
            self.assertEqual(status, 202)
            self.assertEqual(data["provinces"], ["浙江省"])
            for _ in range(30):
                _, sync = self._get("/api/sync/status")
                if sync["state"] != "running":
                    break
                time.sleep(0.02)
        finally:
            daily.sync_subscribed_sources = original

        self.assertEqual(sync["state"], "done")
        self.assertEqual(sync["sources"][0]["source_id"], "zj-shengkao")

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
        """保存（供应商+Key+模型）→ 只回传脱敏 key；视图不含 base_url（后端封装）。"""
        status, data = self._post("/api/llm", {"provider": "deepseek", "api_key": "sk-webtest-abcdef1234", "model": "deepseek-chat"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved"]["api_key_masked"], "sk-w****1234")
        self.assertEqual(data["saved"]["provider"], "deepseek")
        self.assertNotIn("sk-webtest-abcdef1234", json.dumps(data))
        self.assertNotIn("base_url", json.dumps(data))  # 接口地址不出后端

        status, view = self._get("/api/llm")
        self.assertEqual(status, 200)
        self.assertTrue(view["configured"])
        self.assertEqual(view["api_key_masked"], "sk-w****1234")
        self.assertEqual(view["model"], "deepseek-chat")
        self.assertEqual(view["provider"], "deepseek")
        self.assertNotIn("base_url", view)

    def test_llm_unknown_provider_400(self):
        """未知供应商拒绝保存（base_url 只能来自后端注册表）。"""
        status, data = self._post("/api/llm", {"provider": "no-such-vendor", "api_key": "sk-x", "model": "m"})
        self.assertEqual(status, 400)

    def test_llm_providers_list(self):
        """供应商列表：含模型与取 Key 入口，不含 base_url。"""
        status, data = self._get("/api/llm/providers")
        self.assertEqual(status, 200)
        ids = [p["id"] for p in data["providers"]]
        self.assertIn("deepseek", ids)
        for p in data["providers"]:
            self.assertTrue(p["models"])
            self.assertTrue(p["key_url"].startswith("http"))
        self.assertNotIn("base_url", json.dumps(data))

    def test_llm_verify_validation(self):
        """verify：未知供应商 400；空 Key 400（不打真实外网）。"""
        status, _ = self._post("/api/llm/verify", {"provider": "nope", "api_key": "sk-x"})
        self.assertEqual(status, 400)
        status, _ = self._post("/api/llm/verify", {"provider": "deepseek", "api_key": ""})
        self.assertEqual(status, 400)

    def test_llm_empty_key_keeps_saved(self):
        """Key 留空只改模型 → 旧 Key 保留（前端"留空不修改"）。"""
        self._post("/api/llm", {"provider": "deepseek", "api_key": "sk-keep-987654321", "model": "deepseek-chat"})
        status, data = self._post("/api/llm", {"provider": "deepseek", "api_key": "", "model": "deepseek-reasoner"})
        self.assertEqual(status, 200)
        status, view = self._get("/api/llm")
        self.assertEqual(view["model"], "deepseek-reasoner")
        self.assertEqual(view["api_key_masked"], "sk-k****4321")  # 旧 key 未被清空

    def test_llm_connection_test_bad_base_url(self):
        """连通测试打到不可达地址 → ok=False（不发真实外网请求）。"""
        from app.store.db import save_llm_config

        save_llm_config(self.db_file, api_key="sk-webtest-abcdef1234", base_url="http://127.0.0.1:9", provider="deepseek")
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
        # 正式岗位库不再播种样例数据；当前是否有“可报”由真实公告和用户档案决定，
        # 因此只固化接口口径，不把历史样例岗位当作测试前提。
        self.assertIn("jobs_total", c)
        self.assertIn("unverified_no_deadline", c)
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

    def test_match_never_leaks_expired_or_result_publication(self):
        """API 保护线：所有展示分支共享失效过滤；无截止日只进入待核验。"""
        import app.web.server as web
        from datetime import date, timedelta
        from app.models.job import Job

        today = date.today()
        rows = [
            Job(id="open", path=4, title="仍在报名岗位", employer="某单位", apply_deadline=today + timedelta(days=2)),
            Job(id="past", path=4, title="已截止岗位", employer="某单位", apply_deadline=today - timedelta(days=1)),
            Job(id="result", path=4, title="关于拟录取人员名单公示", employer="某单位"),
            Job(id="unknown", path=4, title="未写截止日岗位", employer="某单位"),
        ]
        original = web._get_jobs
        web._get_jobs = lambda _state: (rows, 0)
        try:
            self._post("/api/profile", {"name": "visibility-test", "education": []})
            status, data = self._post("/api/match", {})
        finally:
            web._get_jobs = original

        self.assertEqual(status, 200)
        payload = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("已截止岗位", payload)
        self.assertNotIn("拟录取人员名单公示", payload)
        self.assertEqual(data["counts"]["hidden"]["expired"], 1)
        self.assertEqual(data["counts"]["hidden"]["invalid_result_publication"], 1)
        self.assertEqual(data["counts"]["jobs_total"], 1)
        self.assertEqual(data["counts"]["unverified_no_deadline"], 1)
        self.assertTrue(any(x["title"] == "未写截止日岗位" for x in data["strict"]["pending"]))
        self.assertFalse(any(x["title"] == "未写截止日岗位" for x in data["strict"]["eligible"]))


if __name__ == "__main__":
    unittest.main()
