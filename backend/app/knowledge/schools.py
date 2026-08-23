# 双一流高校知识表：官方 147 所名单（2022 第二轮）加载与安全匹配
# 匹配策略（由严到宽，宁缺勿滥）：
#   1) 归一化精确匹配（全角/半角括号、空白差异归一），命中返回官方原名
#   2) 别名表匹配（"人大"→"中国人民大学" 等非子串型简称；歧义简称不收录）
#   3) 受限前缀：仅当输入以 大学/学院 结尾且长度≥4 时，允许 输入 ⊂ 官方校名
#      （如"中国石油大学"→ 华东/北京两校区均双一流；反向绝不匹配——
#       "浙江大学城市学院"不会因母体"浙江大学"而误判）
#   查不到 → None（未知），绝不返回 False：未知 ≠ 不是双一流。
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Optional, Set

_SCHOOL_FILE = "double_first_class.csv"


def _find_repo_root(start: Optional[Path] = None) -> Path:
    cur = (start or Path(__file__)).resolve()
    for p in [cur, *cur.parents]:
        if (p / "config" / "schools" / _SCHOOL_FILE).exists():
            return p
    raise FileNotFoundError("未找到 config/schools/" + _SCHOOL_FILE)


def _normalize(name: str) -> str:
    return (
        name.replace("（", "(").replace("）", ")").replace(" ", "").replace("\u3000", "").strip()
    )


class SchoolTable:
    def __init__(self) -> None:
        self.canonical: Dict[str, str] = {}  # 归一化校名 -> 官方原名（展示用）
        self.aliases: Dict[str, str] = {}  # 归一化别名 -> 归一化校名

    def __len__(self) -> int:
        return len(self.canonical)

    def add(self, name: str, aliases: str = "") -> None:
        n = _normalize(name)
        if n and n not in self.canonical:
            self.canonical[n] = name
        for a in filter(None, (x.strip() for x in aliases.split("/"))):
            a = _normalize(a)
            if a and a not in self.canonical:
                self.aliases[a] = n

    def lookup(self, school: str) -> Optional[str]:
        """命中返回官方校名（即：是双一流）；查不到返回 None（未知，不等于不是）。"""
        if not school or not school.strip():
            return None
        s = _normalize(school)
        if not s:
            return None
        if s in self.canonical:
            return self.canonical[s]
        if s in self.aliases:
            return self.canonical.get(self.aliases[s]) or self.aliases[s]
        # 受限前缀：输入本身长得像校名（以 大学/学院 结尾且≥4字），且是官方校名的前缀
        if len(s) >= 4 and s.endswith(("大学", "学院")):
            for n, orig in self.canonical.items():
                if n.startswith(s):
                    return orig
        return None


_TABLE: Optional[SchoolTable] = None


def load_schools(root: Optional[Path] = None) -> SchoolTable:
    """加载双一流名单（模块级缓存）。root 可为仓库根或 config/schools 目录。"""
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    base = Path(root) if root else None
    if base is None:
        base = _find_repo_root() / "config" / "schools"
    elif (base / _SCHOOL_FILE).exists():
        pass  # 直接给了 schools 目录
    elif (base / "config" / "schools" / _SCHOOL_FILE).exists():
        base = base / "config" / "schools"
    else:
        base = _find_repo_root(base) / "config" / "schools"
    table = SchoolTable()
    with open(base / _SCHOOL_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(line for line in f if not line.startswith("#")):
            table.add(row.get("name", ""), row.get("aliases", "") or "")
    _TABLE = table
    return table
