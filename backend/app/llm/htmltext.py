# HTML → 干净文本（零依赖，stdlib HTMLParser）
# 用途：C 类公告页在送 LLM 前的降噪。保留标题层级、链接锚文本（公告标题在 <a> 里），
# 去掉 script/style/noscript 与导航噪音；超长文本截断（LLM 上下文与成本控制）。
from __future__ import annotations

import re
from html.parser import HTMLParser

MAX_CHARS = 12000  # 送 LLM 的文本上限（中文公告 95% 在 6k 字内）

_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "li", "tr", "table",
    "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "ul", "ol",
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self.parts.append("《")  # 链接锚文本定界，帮助 LLM 识别"这是标题链接"

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self.parts.append("》")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def html_to_text(html: str, max_chars: int = MAX_CHARS) -> str:
    p = _Extractor()
    try:
        p.feed(html)
    except Exception:
        pass  # 病态 HTML：尽力而为
    text = "".join(p.parts)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（超长截断）"
    return text
