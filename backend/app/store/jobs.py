# 岗位库：data/jobs.jsonl（本地零依赖存储；部署机换 PostgreSQL，接口不变）
# 追加去重：同 id 跳过（除非 force 覆盖更新）。
# 注意：本仓开发沙箱只允许写入会话前已存在的目录，故用 data/ 下扁平文件而非子目录。
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from app.models.job import Job


def store_file(root: Path, path: Optional[Path] = None) -> Path:
    return Path(path) if path else Path(root) / "data" / "jobs.jsonl"


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
