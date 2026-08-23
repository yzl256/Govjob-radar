# 知识库：专业目录加载与查询
# 关键设计：三套目录代码空间独立且存在同码异义（本科 0809=计算机类，
# 学术 0809=电子科学与技术），一切匹配必须在明确目录下进行。
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Optional

CATALOG_UNDERGRAD = "undergraduate"
CATALOG_ACADEMIC = "academic"
CATALOG_PROFESSIONAL = "professional"


def normalize_code(code: str) -> str:
    """取代码纯数字部分（本科含 K/T 后缀的去掉后缀）。"""
    return re.sub(r"\D", "", code or "")


class Catalog:
    def __init__(self, name: str):
        self.name = name
        self.majors: Dict[str, str] = {}  # code -> 专业名
        self.class_nodes: Dict[str, str] = {}  # 4位 类/一级学科 -> 名
        self.discipline_nodes: Dict[str, str] = {}  # 2位 门类 -> 名
        self.parents: Dict[str, str] = {}  # 6位领域码 -> 4位母类别码（专业学位专用）

    def add_major(self, code: str, name: str) -> None:
        self.majors[normalize_code(code)] = name

    def add_parent(self, code: str, parent: str) -> None:
        self.parents[normalize_code(code)] = normalize_code(parent)

    def add_class(self, code: str, name: str) -> None:
        self.class_nodes[normalize_code(code)] = name

    def add_discipline(self, code: str, name: str) -> None:
        self.discipline_nodes[normalize_code(code)] = name

    def has_node(self, prefix: str) -> bool:
        p = normalize_code(prefix)
        return p in self.class_nodes or p in self.discipline_nodes

    def __len__(self) -> int:
        return len(self.majors)


def _repo_root() -> Path:
    """从本文件向上找包含 config/majors 的目录。"""
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "config" / "majors").is_dir():
            return parent
    raise FileNotFoundError("未找到 config/majors 目录（仓库根）")


def _read_csv(path: Path):
    with path.open(encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return csv.DictReader(lines)


def load_catalogs(root: Optional[Path] = None) -> Dict[str, Catalog]:
    base = (Path(root) / "config" / "majors") if root else _repo_root() / "config" / "majors"

    cat_u = Catalog(CATALOG_UNDERGRAD)
    for row in _read_csv(base / "undergraduate.csv"):
        cat_u.add_major(row["code"], row["name"])
        cat_u.add_class(row["class_code"], row["class_name"])
        cat_u.add_discipline(row["discipline_code"], row["discipline_name"])

    cat_a = Catalog(CATALOG_ACADEMIC)
    for row in _read_csv(base / "academic.csv"):
        cat_a.add_major(row["code"], row["name"])
        cat_a.add_class(row["code"], row["name"])  # 一级学科即节点
        cat_a.add_discipline(row["discipline_code"], row["discipline_name"])

    cat_p = Catalog(CATALOG_PROFESSIONAL)
    for row in _read_csv(base / "professional.csv"):
        code = normalize_code(row["code"])
        cat_p.add_major(code, row["name"])
        cat_p.add_discipline(row["group_code"], row["group_name"])
        if len(code) == 6:  # 研招网领域码 → 母类别（如 085411 → 0854）
            cat_p.add_parent(code, code[:4])

    return {CATALOG_UNDERGRAD: cat_u, CATALOG_ACADEMIC: cat_a, CATALOG_PROFESSIONAL: cat_p}


def infer_catalog(level_code: str, major_code: str, catalogs: Dict[str, Catalog]) -> Optional[str]:
    """由学历层次+代码推断所属目录。本科→undergraduate；
    研究生→代码命中专业学位目录（含6位领域码）则 professional，否则 academic。"""
    code = normalize_code(major_code)
    if level_code == "本科":
        return CATALOG_UNDERGRAD
    if level_code in ("硕士", "博士"):
        if code and (code in catalogs[CATALOG_PROFESSIONAL].majors):
            return CATALOG_PROFESSIONAL
        return CATALOG_ACADEMIC
    return None  # 大专：专科目录暂未建（seed 限制），返回 None
