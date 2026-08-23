import unittest
from datetime import date, timedelta
from pathlib import Path

from app.models.job import EduRequire, Job
from app.pipeline.daily import filter_active_jobs

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 8, 23)


def mk(deadline=None):
    return Job(path=4, title="岗", edu_require=EduRequire(), apply_deadline=deadline)


class TestFilterActiveJobs(unittest.TestCase):
    def test_expired_excluded(self):
        jobs = [mk(TODAY - timedelta(days=1)), mk(date(2025, 12, 8))]
        self.assertEqual(filter_active_jobs(jobs, TODAY), [])

    def test_today_and_future_kept(self):
        future = [mk(TODAY), mk(TODAY + timedelta(days=30))]  # 当天截止仍可报
        self.assertEqual(filter_active_jobs(future, TODAY), future)

    def test_no_deadline_kept(self):
        jobs = [mk(None), mk(None)]
        self.assertEqual(filter_active_jobs(jobs, TODAY), jobs)  # 无法判定不误删

    def test_mixed(self):
        jobs = [mk(None), mk(TODAY - timedelta(days=1)), mk(TODAY + timedelta(days=1))]
        active = filter_active_jobs(jobs, TODAY)
        self.assertEqual(len(active), 2)
        self.assertNotIn(TODAY - timedelta(days=1), [j.apply_deadline for j in active])

    def test_default_today(self):
        tomorrow = date.today() + timedelta(days=1)
        yesterday = date.today() - timedelta(days=1)
        active = filter_active_jobs([mk(tomorrow), mk(yesterday)])
        self.assertEqual([j.apply_deadline for j in active], [tomorrow])


if __name__ == "__main__":
    unittest.main()
