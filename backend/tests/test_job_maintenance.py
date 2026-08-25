import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.models.job import Job
from app.pipeline.source_review import invalid_source_urls, review_stored_sources
from app.store.jobs import (
    archive_invalid_jobs,
    archive_summary,
    append_jobs,
    load_jobs,
    restore_result_publication_jobs,
)
from app.validity import split_displayable_jobs


TODAY = date(2026, 8, 25)


def job(job_id: str, title: str = "岗位", deadline=None, source_url: str = "") -> Job:
    return Job(
        id=job_id,
        path=4,
        title=title,
        employer="某单位",
        apply_deadline=deadline,
        source_url=source_url,
    )


class TestJobValidity(unittest.TestCase):
    def test_result_publication_and_deadline_are_both_hidden(self):
        rows = [
            job("past", deadline=TODAY - timedelta(days=1)),
            job("today", deadline=TODAY),
            job("unknown"),
            job("result", title="关于派遣人员名单的公示"),
        ]
        active, expired, results = split_displayable_jobs(rows, today=TODAY)
        self.assertEqual([x.id for x in active], ["today", "unknown"])
        self.assertEqual([x.id for x in expired], ["past"])
        self.assertEqual([x.id for x in results], ["result"])


class TestArchiveInvalidJobs(unittest.TestCase):
    def test_moves_only_deterministically_invalid_rows_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data").mkdir()
            source_url = "https://example.test/result"
            rows = [
                job("past", deadline=TODAY - timedelta(days=1)),
                job("today", deadline=TODAY),
                job("unknown"),
                job("source-result", title="普通岗位", source_url=source_url),
            ]
            append_jobs(root, rows)
            # 坏行保留在主文件：清理任务不得为“修复失效数据”顺便吞掉历史资料。
            store = root / "data" / "jobs.jsonl"
            duplicate = job("duplicate", title="完全相同的历史行", source_url=source_url).model_dump_json()
            store.write_text(
                store.read_text(encoding="utf-8") + duplicate + "\n" + duplicate + "\n{not-json}\n",
                encoding="utf-8",
            )

            result = archive_invalid_jobs(root, today=TODAY, invalid_source_urls={source_url})
            self.assertEqual(result.archived_expired, 1)
            self.assertEqual(result.archived_result_publications, 3)
            self.assertEqual([x.id for x in load_jobs(root)], ["today", "unknown"])
            self.assertIn("{not-json}", store.read_text(encoding="utf-8"))

            summary = archive_summary(root)
            self.assertEqual(summary.archived_expired, 1)
            self.assertEqual(summary.archived_result_publications, 3)
            # 第二次不会重复归档，也不会删除无截止日期岗位。
            self.assertEqual(archive_invalid_jobs(root, today=TODAY, invalid_source_urls={source_url}).total, 0)
            files = list((root / "data" / "out" / "archive").glob("jobs_*.jsonl"))
            records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 4)


class TestSourceReview(unittest.TestCase):
    def test_result_source_is_recorded_then_can_be_archived(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data").mkdir()
            url = "https://example.test/announcement"
            append_jobs(root, [job("table-row", title="基层服务岗位", source_url=url)])
            summary = review_stored_sources(
                root,
                fetcher=lambda _url: "<html><title>关于拟录取人员名单公示</title></html>".encode(),
            )
            self.assertEqual(summary.reviewed, 1)
            self.assertEqual(summary.result_publications, 1)
            self.assertEqual(invalid_source_urls(root), {url})

            moved = archive_invalid_jobs(root, today=TODAY, invalid_source_urls=invalid_source_urls(root))
            self.assertEqual(moved.archived_result_publications, 1)
            self.assertEqual(load_jobs(root), [])

            # 归档结论若被来源页复核纠正，可恢复到主库，不需要找备份文件。
            self.assertEqual(restore_result_publication_jobs(root, {url}), 1)
            self.assertEqual([x.id for x in load_jobs(root)], ["table-row"])
            self.assertEqual(archive_summary(root).archived_result_publications, 0)

    def test_result_words_in_page_body_do_not_override_recruitment_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data").mkdir()
            url = "https://example.test/open"
            append_jobs(root, [job("open", title="公开招聘岗位", source_url=url)])
            summary = review_stored_sources(
                root,
                fetcher=lambda _url: (
                    "<html><title>某单位 2026 年公开招聘公告</title>"
                    "<body>相关链接：拟录取人员名单公示</body></html>"
                ).encode(),
            )
            self.assertEqual(summary.result_publications, 0)
            self.assertEqual(invalid_source_urls(root), set())


if __name__ == "__main__":
    unittest.main()
