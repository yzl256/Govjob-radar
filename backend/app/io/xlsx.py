# 零依赖 XLSX 读写器（zipfile + xml.etree）
# 为什么不用 openpyxl：个人工具减少依赖链；职位表是纯文本表格，
# 不需要公式/样式/日期等重能力。真实仓库环境可换 openpyxl，接口不变。
#
# 读取限制（刻意）：
#   - 单元格取文本值（数字转为字符串，公式取缓存值）
#   - 不解析样式/合并单元格元数据（国考表表头行的合并不影响按值找表头）
from __future__ import annotations

import io
import re
import zipfile
from typing import Dict, List
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKGREL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _col_letters(ref: str) -> str:
    m = re.match(r"([A-Z]+)", ref or "")
    return m.group(1) if m else ""


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_workbook(path: str) -> Dict[str, List[List[str]]]:
    """读 xlsx → {sheet名: 行列表}，单元格一律转 str（空单元格为 ""）。"""
    with zipfile.ZipFile(path) as z:
        shared = _read_shared_strings(z)
        sheets = _sheet_targets(z)
        out: Dict[str, List[List[str]]] = {}
        for name, target in sheets:
            with z.open(target) as f:
                out[name] = _parse_sheet(f.read(), shared)
        return out


def _read_shared_strings(z: zipfile.ZipFile) -> List[str]:
    try:
        with z.open("xl/sharedStrings.xml") as f:
            root = ET.fromstring(f.read())
    except KeyError:
        return []
    strings: List[str] = []
    for si in root:
        # <si> 内可能是 <t> 或富文本多个 <r><t>
        buf = []
        for node in si.iter():
            if _local(node.tag) == "t" and node.text:
                buf.append(node.text)
        strings.append("".join(buf))
    return strings


def _sheet_targets(z: zipfile.ZipFile) -> List[tuple[str, str]]:
    with z.open("xl/workbook.xml") as f:
        wb = ET.fromstring(f.read())
    with z.open("xl/_rels/workbook.xml.rels") as f:
        rels = ET.fromstring(f.read())
    rid_to_target = {r.get("Id"): r.get("Target") for r in rels}
    result = []
    for sheet in wb.iter():
        if _local(sheet.tag) != "sheet":
            continue
        rid = sheet.get(f"{_NS_REL}id")
        target = rid_to_target.get(rid, "")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = "xl/" + target
        result.append((sheet.get("name") or target, target))
    return result


def _parse_sheet(data: bytes, shared: List[str]) -> List[List[str]]:
    root = ET.fromstring(data)
    rows: List[List[str]] = []
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        cells: Dict[int, str] = {}
        max_col = -1
        for c in row:
            if _local(c.tag) != "c":
                continue
            idx = _col_index(_col_letters(c.get("r") or ""))
            val = _cell_text(c, shared)
            cells[idx] = val
            max_col = max(max_col, idx)
        rows.append([cells.get(i, "") for i in range(max_col + 1)])
    return rows


def _cell_text(c: ET.Element, shared: List[str]) -> str:
    t = c.get("t")
    if t == "inlineStr":
        buf = []
        for node in c.iter():
            if _local(node.tag) == "t" and node.text:
                buf.append(node.text)
        return "".join(buf)
    v = None
    for node in c:
        if _local(node.tag) == "v" and node.text is not None:
            v = node.text
            break
    if v is None:
        return ""
    if t == "s":
        i = int(v)
        return shared[i] if 0 <= i < len(shared) else ""
    return v  # 数字/布尔等按原文


# ── 极简写入器（仅用于生成测试夹具与 demo 样例）──────────────────
_CTMPL = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WB = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def write_workbook(path: str, sheet_name: str, rows: List[List[str]]) -> None:
    """写单 sheet、inlineStr 单元格的 xlsx（测试/demo 用）。"""
    letters = [chr(65 + i) for i in range(26)] + [
        chr(65 + i // 26 - 1) + chr(65 + i % 26) for i in range(26, 702)
    ]
    body = []
    for r_idx, row in enumerate(rows, 1):
        cells = []
        for c_idx, val in enumerate(row):
            ref = f"{letters[c_idx]}{r_idx}"
            text = escape(str(val if val is not None else ""))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        body.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CTMPL)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", _WB.format(name=escape(sheet_name)))
        z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
