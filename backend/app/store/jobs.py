# 岗位库：data/jobs.jsonl（本地零依赖存储；部署机换 PostgreSQL，接口不变）
# 追加去重：同 id 跳过（除非 force 覆盖更新）。
# 注意：本仓开发沙箱只允许写入会话前已存在的目录，故用 data/ 下扁平文件而非子目录。
from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.models.job import Job
from app.validity import job_invalid_reason


def store_file(root: Path, path: Optional[Path] = None) -> Path:
    return Path(path) if path else Path(root) / "data" / "jobs.jsonl"


def archive_dir(root: Path) -> Path:
    """机器管理的可恢复归档区；绝不触碰用户手工投放的 ``data/inbox``。"""
    return Path(root) / "data" / "out" / "archive"


def archive_file(root: Path, today: Optional[date] = None) -> Path:
    """按月分卷归档，避免一个文件无限增长。"""
    today = today or date.today()
    return archive_dir(root) / f"jobs_{today.strftime('%Y-%m')}.jsonl"


@dataclass(frozen=True)
class ArchiveSummary:
    archived_expired: int = 0
    archived_result_publications: int = 0

    @property
    def total(self) -> int:
        return self.archived_expired + self.archived_result_publications

    def merged(self, other: "ArchiveSummary") -> "ArchiveSummary":
        return ArchiveSummary(
            archived_expired=self.archived_expired + other.archived_expired,
            archived_result_publications=(
                self.archived_result_publications + other.archived_result_publications
            ),
        )


def load_jobs(root: Path, path: Optional[Path] = None) -> List[Job]:
    f = store_file(root, path)
    if not f.exists():
        return []
    jobs: List[Job] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            jobs.append(Job.model_validate_json(line))
        except Exception:
            continue  # 坏行跳过，不拖垮加载
    return jobs


def append_jobs(root: Path, jobs: List[Job], force: bool = False, path: Optional[Path] = None) -> Tuple[int, int]:
    """追加岗位；返回 (新增数, 跳过数)。"""
    f = store_file(root, path)
    existing = {j.id for j in load_jobs(root, path)} if not force else set()
    new = [j for j in jobs if j.id not in existing or force]
    skipped = len(jobs) - len(new)
    if new:
        with open(f, "a", encoding="utf-8") as fh:  # data/ 已存在，无需 mkdir
            for j in new:
                fh.write(j.model_dump_json() + "\n")
    return len(new), skipped


def _archive_keys(root: Path) -> Set[str]:
    """已归档原始行的指纹；用于“写归档后、替换主库前崩溃”的安全重试。"""
    keys: Set[str] = set()
    base = archive_dir(root)
    if not base.exists():
        return keys
    for path in base.glob("jobs_*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = record.get("archive_key") if isinstance(record, dict) else None
            if isinstance(key, str):
                keys.add(key)
    return keys


def archive_summary(root: Path) -> ArchiveSummary:
    """统计已经可恢复归档的岗位，不读取主岗位库。"""
    expired = results = 0
    base = archive_dir(root)
    if not base.exists():
        return ArchiveSummary()
    for path in base.glob("jobs_*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("restored_at"):
                continue
            reason = record.get("archive_reason")
            if reason == "deadline_passed":
                expired += 1
            elif reason == "result_publication":
                results += 1
    return ArchiveSummary(expired, results)


def archive_invalid_jobs(
    root: Path,
    today: Optional[date] = None,
    invalid_source_urls: Optional[Iterable[str]] = None,
    path: Optional[Path] = None,
) -> ArchiveSummary:
    """把有确定性失效证据的岗位移至归档区，主库只留下可继续核验的记录。

    - 只处理 ``jobs.jsonl``，不移动用户的 ``data/inbox/*.xlsx``；混合新旧岗位表
      仍可逐行被展示层过滤，避免误删用户附件。
    - 无截止日期的岗位不归档。
    - 坏行原样留在主文件，避免清理任务意外吞掉无法解析的历史数据。
    """
    root = Path(root)
    today = today or date.today()
    source = store_file(root, path)
    if not source.exists():
        return ArchiveSummary()

    try:
        raw_lines = source.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ArchiveSummary()

    invalid_urls = set(invalid_source_urls or [])
    keep_lines: List[str] = []
    to_archive: List[tuple[str, Job, str, str]] = []
    occurrences: Dict[str, int] = {}
    for raw_line in raw_lines:
        if not raw_line.strip():
            keep_lines.append(raw_line)
            continue
        try:
            job = Job.model_validate_json(raw_line)
        except Exception:
            keep_lines.append(raw_line)
            continue
        reason = job_invalid_reason(job, today=today, invalid_source_urls=invalid_urls)
        if reason is None:
            keep_lines.append(raw_line)
        else:
            # 同一岗位表里可能有完全相同的多行。不能只用 raw JSON 哈希做 key，
            # 否则归档时会把“重复行”悄悄压成一条；同一文件内的出现次序可让
            # 重试保持幂等，同时保留每一行的数量。
            base_key = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
            ordinal = occurrences.get(base_key, 0)
            occurrences[base_key] = ordinal + 1
            to_archive.append((raw_line, job, reason, f"{base_key}:{ordinal}"))

    if not to_archive:
        return ArchiveSummary()

    target = archive_file(root, today)
    target.parent.mkdir(parents=True, exist_ok=True)
    known_keys = _archive_keys(root)
    archived_expired = archived_results = 0
    # 先追加归档（可由 archive_key 幂等重试），成功后才原子替换主库，宁可重复
    # 尝试也不让一次中断丢掉原始岗位。
    try:
        with target.open("a", encoding="utf-8") as fh:
            for raw_line, job, reason, key in to_archive:
                if key not in known_keys:
                    record = {
                        "archive_key": key,
                        "archived_at": datetime.now().isoformat(timespec="seconds"),
                        "archive_reason": reason,
                        "source_url": job.source_url,
                        "job": job.model_dump(mode="json"),
                    }
                    fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    known_keys.add(key)
                if reason == "deadline_passed":
                    archived_expired += 1
                elif reason == "result_publication":
                    archived_results += 1
    except OSError:
        return ArchiveSummary()

    # 原始行不重新序列化，既保留未知字段，也不会因模型升级改变无关岗位。
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=source.parent, delete=False, suffix=".tmp"
        ) as fh:
            tmp = Path(fh.name)
            if keep_lines:
                fh.write("\n".join(keep_lines) + "\n")
        tmp.replace(source)
    except OSError:
        # 已写归档但未成功替换时，下一轮会依据 archive_key 安全完成替换。
        try:
            tmp.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        return ArchiveSummary()
    return ArchiveSummary(archived_expired, archived_results)


def restore_result_publication_jobs(root: Path, source_urls: Iterable[str]) -> int:
    """恢复被错误标为“结果公示”的来源岗位。

    归档不是删除：若来源页复核结论被纠正，可以按 URL 将相应岗位回写主库，
    并在归档记录上标注恢复时间。仅恢复 ``result_publication`` 原因，已截止岗位
    仍然保持归档，避免把真实过期岗位重新展示。
    """
    root = Path(root)
    wanted = {url for url in source_urls if url}
    if not wanted:
        return 0
    base = archive_dir(root)
    if not base.exists():
        return 0

    changes: Dict[Path, List[str]] = {}
    restore_rows: List[str] = []
    restored_at = datetime.now().isoformat(timespec="seconds")
    for path in base.glob("jobs_*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        updated: List[str] = []
        changed = False
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                updated.append(line)
                continue
            if (
                isinstance(record, dict)
                and record.get("archive_reason") == "result_publication"
                and record.get("source_url") in wanted
                and not record.get("restored_at")
            ):
                try:
                    job = Job.model_validate(record.get("job") or {})
                except Exception:
                    updated.append(line)
                    continue
                restore_rows.append(job.model_dump_json())
                record["restored_at"] = restored_at
                updated.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                changed = True
            else:
                updated.append(line)
        if changed:
            changes[path] = updated

    if not restore_rows:
        return 0

    # 主库先补齐缺失行。若上次进程恰好在“补主库”和“标归档”之间中断，
    # 用规范化行计数避免下一次恢复时重复追加。
    source = store_file(root)
    existing = Counter(j.model_dump_json() for j in load_jobs(root))
    required = Counter(restore_rows)
    rows_to_append: List[str] = []
    for row, count in required.items():
        rows_to_append.extend([row] * max(0, count - existing.get(row, 0)))
    if rows_to_append:
        with source.open("a", encoding="utf-8") as fh:
            for row in rows_to_append:
                fh.write(row + "\n")

    # 每卷分别原子替换；若其中一卷失败，下次调用会因上面的计数保护而只补标记。
    for path, updated in changes.items():
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as fh:
            tmp = Path(fh.name)
            if updated:
                fh.write("\n".join(updated) + "\n")
        tmp.replace(path)
    return len(restore_rows)
