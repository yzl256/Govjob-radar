# 源站注册表加载 + 调度核心（P2）
# 职责：按用户订阅省份决定启用哪些源站；输出源站规格与健康记录。
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.io.miniyaml import load_yaml_file


@dataclass
class SourceSpec:
    id: str
    name: str
    path: int
    tier: str
    region: object = None
    entry: Optional[str] = None
    apply_entry: Optional[str] = None
    extractor: str = "llm"
    schedule: dict = field(default_factory=dict)
    status: str = "pending_survey"
    notes: str = ""
    province_file: str = ""  # 来源文件（national 或 省名）

    @property
    def is_active_source(self) -> bool:
        return self.status in ("pending_survey", "surveyed", "active") and bool(self.entry)


def _province_of_file(path: Path) -> str:
    return "national" if path.stem == "national" else path.stem


def load_sources(
    root: Path, subscribed_provinces: Optional[List[str]] = None
) -> List[SourceSpec]:
    """全国源恒启用；省源按订阅名单启用（文件不存在则静默跳过）。"""
    base = Path(root) / "config" / "sources"
    specs: List[SourceSpec] = []
    wanted = set(subscribed_provinces or [])
    for f in sorted(base.glob("*.yaml")):
        if f.stem.startswith("_"):
            continue
        province = _province_of_file(f)
        if province != "national" and province not in wanted:
            continue
        try:
            data = load_yaml_file(f)
        except Exception as e:  # 配置损坏不拖垮调度
            print(f"[sources] 解析失败 {f.name}: {e}")
            continue
        for raw in data.get("sources", []):
            specs.append(
                SourceSpec(
                    id=raw.get("id", ""),
                    name=raw.get("name", ""),
                    path=int(raw.get("path", 0)),
                    tier=raw.get("tier", "C"),
                    region=raw.get("region"),
                    entry=raw.get("entry"),
                    apply_entry=raw.get("apply_entry"),
                    extractor=raw.get("extractor", "llm"),
                    schedule=raw.get("schedule") or {},
                    status=raw.get("status", "pending_survey"),
                    notes=raw.get("notes", "") or "",
                    province_file=province,
                )
            )
    return specs


@dataclass
class HealthRecord:
    source_id: str
    ok: bool
    detail: str
    fetched_items: int = 0


def load_profiles(root: Path) -> List[dict]:
    """config/profiles/*.json → 原始 dict 列表（保持字段宽松）。"""
    import json

    out = []
    for f in sorted((Path(root) / "config" / "profiles").glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[profiles] 读取失败 {f.name}: {e}")
    return out
