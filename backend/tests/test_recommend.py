import unittest

from app.matching.recommend import recommend_soe, split_recommendations
from app.models.job import Job
from app.models.profile import UserProfile


class TestSoeRecommendation(unittest.TestCase):
    def setUp(self):
        self.profile = UserProfile(career_interests=["数据产品"])

    def test_technical_role_is_strong_recommendation(self):
        job = Job(path=6, title="数据平台研发工程师", responsibilities="负责数据平台与软件开发")
        level, reason = recommend_soe(job, self.profile)
        self.assertEqual(level, "strong")
        self.assertIn("技术领域", reason)

    def test_irrelevant_role_is_not_recommended(self):
        job = Job(path=6, title="行政专员", responsibilities="负责行政接待和会务")
        self.assertEqual(recommend_soe(job, self.profile)[0], None)

    def test_pending_announcement_stays_out_of_main_recommendations(self):
        job = Job(path=6, title="管培生", verification_status="pending")
        buckets = split_recommendations([job], self.profile)
        self.assertFalse(buckets["strong"])
        self.assertEqual(len(buckets["pending"]), 1)


if __name__ == "__main__":
    unittest.main()
