# 零依赖 YAML 子集加载器
# 为什么不用 PyYAML：环境 pip 受限（见 README）；源站注册表只用 YAML 的一个
# 稳定子集，这里按需实现并用真实配置文件做回归测试。
# 支持子集（config/sources/*.yaml 的全部用法）：
#   注释（整行/行尾）、映射、序列（含 "- key: value" 起始的映射项、纯量项、
#   嵌套序列）、内联流式 {k: v} / [a, b]、块标量 | |- > >-、
#   引号字符串、int/float/bool/null、中文键值。
from __future__ import annotations

from typing import Any, List, Tuple, Union


def _strip_comment(line: str) -> str:
    out = []
    quote = None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(text: str) -> Any:
    t = text.strip()
    if not t or t in ("null", "Null", "~"):
        return None
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        return t[1:-1]
    if t in ("true", "True"):
        return True
    if t in ("false", "False"):
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    # 内联流式
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        if not inner:
            return []
        return [_scalar(x) for x in _split_flow(inner)]
    if t.startswith("{") and t.endswith("}"):
        inner = t[1:-1].strip()
        out = {}
        for pair in _split_flow(inner):
            k, _, v = pair.partition(":")
            out[_scalar(k) if _scalar(k) is not None else k.strip()] = _scalar(v)
        return out
    return t


def _split_flow(text: str) -> List[str]:
    parts, buf, quote, depth = [], [], None, 0
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


class _Lines:
    def __init__(self, text: str):
        self.items: List[Tuple[int, str]] = []
        for raw in text.splitlines():
            line = _strip_comment(raw.replace("\t", "    "))
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            self.items.append((indent, line.strip()))
        self.i = 0

    def peek(self) -> Union[Tuple[int, str], None]:
        return self.items[self.i] if self.i < len(self.items) else None

    def next(self) -> Tuple[int, str]:
        item = self.items[self.i]
        self.i += 1
        return item


def _is_seq_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def parse(text: str) -> Any:
    lines = _Lines(text)
    if lines.peek() is None:
        return {}
    indent = lines.peek()[0]
    obj, _ = _parse_block(lines, indent)
    return obj


def _parse_block(lines: _Lines, indent: int) -> Tuple[Any, int]:
    ind, content = lines.peek()
    if _is_seq_item(content):
        return _parse_seq(lines, indent)
    return _parse_map(lines, indent)


def _parse_map(lines: _Lines, indent: int) -> Tuple[dict, int]:
    out: dict = {}
    while True:
        peeked = lines.peek()
        if peeked is None:
            break
        ind, content = peeked
        if ind != indent or _is_seq_item(content):
            break
        lines.next()
        key, sep, rest = content.partition(":")
        key = key.strip().strip("'\"")
        rest = rest.strip()
        if not sep:
            break  # 非法行，防御
        if rest == "":
            nxt = lines.peek()
            if nxt is None or nxt[0] <= indent:
                out[key] = None
            else:
                out[key], _ = _parse_block(lines, nxt[0])
        elif rest in ("|", "|-", ">", ">-"):
            block: List[str] = []
            while True:
                nxt = lines.peek()
                if nxt is None or nxt[0] <= indent:
                    break
                block.append(lines.next()[1])
            joiner = "\n" if rest.startswith("|") else " "
            text = joiner.join(block).strip()
            if rest == "|":
                text += "\n"
            out[key] = text
        else:
            out[key] = _scalar(rest)
    return out, lines.i


def _parse_seq(lines: _Lines, indent: int) -> Tuple[list, int]:
    out: list = []
    while True:
        peeked = lines.peek()
        if peeked is None:
            break
        ind, content = peeked
        if ind != indent or not _is_seq_item(content):
            break
        lines.next()
        rest = content[1:].strip()  # "-" 之后
        if rest == "":
            nxt = lines.peek()
            if nxt is not None and nxt[0] > indent:
                out.append(_parse_block(lines, nxt[0])[0])
            else:
                out.append(None)
            continue
        # "- key: value"：把首行视为缩进+2 的虚拟行，与其后同块行一起递归
        nxt = lines.peek()
        if ": " in rest or rest.endswith(":"):
            virtual_indent = indent + 2
            sub = _Lines("")
            sub.items = [(virtual_indent, rest)]
            if nxt is not None and nxt[0] > indent:
                while True:
                    p = lines.peek()
                    if p is None or p[0] <= indent:
                        break
                    sub.items.append(lines.next())
            sub.i = 0
            out.append(_parse_map(sub, virtual_indent)[0])
        else:
            out.append(_scalar(rest))
    return out, lines.i


def load_yaml_file(path) -> Any:
    from pathlib import Path

    return parse(Path(path).read_text(encoding="utf-8"))
