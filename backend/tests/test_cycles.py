import unittest
from datetime import date

from app.knowledge.cycles import _in_window, _next_open, track_board
from app.models.profile import UserProfile


class TestCyclesModel(unittest.TestCase):
    """赛道周期看板：阶段判定（8月→国考备考/人才引进可投）、跨年窗口、档案交互提示。"""

    def test_ten_tracks_complete(self):
        board = track_board(today=date(2026, 8, 23))
        self.assertEqual([b["path"] for b in board], list(range(1, 11)))
        for b in board:
            self.assertIn(b["phase"], ("open", "prep"))
            self.assertTrue(b["desc"] and b["advice"] and b["cycle"])

    def test_august_snapshot(self):
        """2026-08-23：国考/省考/统考未到窗口→备考；滚动赛道→可投递。"""
        board = {b["path"]: b for b in track_board(today=date(2026, 8, 23))}
        self.assertEqual(board[2]["phase"], "prep")  # 国考 10 月
        self.assertIn("2026年10月", board[2]["window"])
        self.assertGreater(board[2]["countdown"], 0)
        self.assertEqual(board[3]["phase"], "prep")  # 省考 12-1 月（跨年窗口）
        self.assertIn("2026年12月", board[3]["window"])
        for p in (4, 5, 6, 7, 10):  # 事业编散招/人才引进/国企/文职技能岗/辅导员社区
            self.assertEqual(board[p]["phase"], "open", f"path={p} 8月应可投递")
        self.assertIn("全年滚动", board[5]["window"])
        self.assertEqual(board[8]["phase"], "prep")  # 三支一扶 4-6 月已过
        self.assertIn("2027年4月", board[8]["window"])  # 下一轮明年

    def test_window_in_progress(self):
        """10 月处于国考报名窗口 → open。"""
        board = {b["path"]: b for b in track_board(today=date(2026, 10, 15))}
        self.assertEqual(board[2]["phase"], "open")
        self.assertIn("窗口进行中", board[2]["window"])

    def test_cross_year_window_january(self):
        """省考窗口 12-1 月跨年：1 月仍在窗口内。"""
        board = {b["path"]: b for b in track_board(today=date(2027, 1, 10))}
        self.assertEqual(board[3]["phase"], "open")

    def test_xuandiao_user_note_graduated(self):
        """已毕业用户 → 选调卡提示广东口径已关闭。"""
        profile = UserProfile.model_validate(
            {
                "name": "t",
                "education": [{"level": "硕士", "major_code": "085411", "graduation_date": "2026-06-30"}],
            }
        )
        board = {b["path"]: b for b in track_board(profile, date(2026, 8, 23))}
        self.assertIn("已关闭", board[1]["user_note"])
        self.assertIn("2026年6月", board[1]["user_note"])
        self.assertIsNone(board[2]["user_note"])  # 其他赛道无交互提示

    def test_xuandiao_user_note_inschool(self):
        """未填毕业时间 → 不误判关闭。"""
        profile = UserProfile.model_validate({"name": "t", "education": [{"level": "硕士", "major_code": "085411"}]})
        board = {b["path"]: b for b in track_board(profile, date(2026, 8, 23))}
        self.assertIsNone(board[1]["user_note"])

    def test_helpers(self):
        self.assertTrue(_in_window(12, (12, 1)))
        self.assertTrue(_in_window(1, (12, 1)))
        self.assertFalse(_in_window(8, (12, 1)))
        self.assertEqual(_next_open(date(2026, 8, 23), 10), date(2026, 10, 1))
        self.assertEqual(_next_open(date(2026, 8, 23), 4), date(2027, 4, 1))  # 已过 → 明年
        self.assertEqual(_next_open(date(2026, 12, 1), 12), date(2027, 12, 1))  # 当月已进入窗口外的处理


class TestSplitByDeadline(unittest.TestCase):
    """「可报名投递 / 已截止备考」岗位二分。"""

    def test_split(self):
        from app.models.job import Job
        from app.pipeline.daily import split_by_deadline

        jobs = [
            Job(path=2, title="在招A", apply_deadline=date(2026, 10, 25)),
            Job(path=2, title="无截止B", apply_deadline=None),
            Job(path=2, title="今日截止C", apply_deadline=date(2026, 8, 23)),  # 当天仍可报
            Job(path=1, title="已过D", apply_deadline=date(2025, 12, 8)),
        ]
        active, expired = split_by_deadline(jobs, date(2026, 8, 23))
        self.assertEqual([j.title for j in active], ["在招A", "无截止B", "今日截止C"])
        self.assertEqual([j.title for j in expired], ["已过D"])


if __name__ == "__main__":
    unittest.main()
