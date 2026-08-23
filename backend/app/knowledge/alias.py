# 专业别名表：模糊写法 → 各目录代码集合
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Set

from app.knowledge.catalogs import normalize_code


class AliasTable:
    def __init__(self, data: dict):
        self._aliases: Dict[str, dict] = data.get("aliases", {})
        self._families: Dict[str, dict] = {
            k: v for k, v in data.get("class_families", {}).items() if not k.startswith("_")
        }

    def class_family(self, undergrad_class: str) -> Optional[dict]:
        """本科专业类(4位) → {academic:set, professional:set} 同族研究生代码；未收录返回 None。"""
        fam = self._families.get(normalize_code(undergrad_class))
        if fam is None:
            return None
        return {
            cat: {normalize_code(c) for c in codes}
            for cat, codes in fam.items()
        }

    def get(self, text: str) -> Optional[dict]:
        """返回 {catalog: {"codes": set, "prefixes": set}} 或 None（未知别名）。"""
        entry = self._aliases.get((text or "").strip())
        if entry is None:
            return None
        out = {}
        for catalog, spec in entry.items():
            codes = {normalize_code(c) for c in spec.get("codes", [])}
            prefixes = {normalize_code(p) for p in spec.get("prefixes", [])}
            out[catalog] = {"codes": codes, "prefixes": prefixes}
        return out

    def known_aliases(self) -> Set[str]:
        return set(self._aliases.keys())


def load_aliases(path: Optional[Path] = None) -> AliasTable:
    """path 可以是别名表 json 文件本身，或仓库根目录（自动定位 config/majors/major_aliases.json）。"""
    p = Path(path) if path is not None else None
    if p is None or p.is_dir():
        from app.knowledge.catalogs import _repo_root

        root = p if p is not None else _repo_root()
        p = root / "config" / "majors" / "major_aliases.json"
    with p.open(encoding="utf-8") as f:
        return AliasTable(json.load(f))
