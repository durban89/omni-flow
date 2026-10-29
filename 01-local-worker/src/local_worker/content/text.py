from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_IGNORED_TAGS = {"iframe", "noscript", "script", "style", "svg"}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div",
    "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
    "nav", "ol", "p", "pre", "section", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "ul",
}
_HTML_PATTERN = re.compile(r"<\s*[a-zA-Z][^>]*>")


class _HtmlToMarkdownParser(HTMLParser):
    """Converts HTML markup to clean, structured Markdown."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0
        self._list_stack: list[dict[str, int]] = []
        self._link_stack: list[dict[str, str]] = []
        self._in_pre = False
        self._in_code = False
        self._in_blockquote = False

    @property
    def markdown(self) -> str:
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return

        attr_dict = {k.lower(): v for k, v in attrs if k}

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self._parts.append(f"\n\n{'#' * level} ")
        elif tag == "p":
            self._parts.append("\n\n")
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "hr":
            self._parts.append("\n\n---\n\n")
        elif tag in {"strong", "b"}:
            self._parts.append("**")
        elif tag in {"em", "i"}:
            self._parts.append("*")
        elif tag == "code":
            if not self._in_pre:
                self._parts.append("`")
            self._in_code = True
        elif tag == "pre":
            self._in_pre = True
            self._parts.append("\n\n```\n")
        elif tag == "blockquote":
            self._in_blockquote = True
            self._parts.append("\n\n> ")
        elif tag == "ul":
            self._list_stack.append({"type": "ul", "index": 0})
            self._parts.append("\n")
        elif tag == "ol":
            self._list_stack.append({"type": "ol", "index": 0})
            self._parts.append("\n")
        elif tag == "li":
            indent = "  " * max(0, len(self._list_stack) - 1)
            if self._list_stack:
                current = self._list_stack[-1]
                if current["type"] == "ol":
                    current["index"] += 1
                    self._parts.append(f"\n{indent}{current['index']}. ")
                else:
                    self._parts.append(f"\n{indent}- ")
            else:
                self._parts.append("\n- ")
        elif tag == "a":
            href = attr_dict.get("href") or ""
            title = attr_dict.get("title") or ""
            self._link_stack.append({"href": href, "title": title, "text": ""})
        elif tag == "img":
            src = attr_dict.get("src") or ""
            alt = attr_dict.get("alt") or "image"
            if src:
                self._parts.append(f"![{alt}]({src})")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self._parts.append("\n\n")
        elif tag in {"strong", "b"}:
            self._parts.append("**")
        elif tag in {"em", "i"}:
            self._parts.append("*")
        elif tag == "code":
            if not self._in_pre:
                self._parts.append("`")
            self._in_code = False
        elif tag == "pre":
            self._in_pre = False
            self._parts.append("\n```\n\n")
        elif tag == "blockquote":
            self._in_blockquote = False
            self._parts.append("\n\n")
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            self._parts.append("\n")
        elif tag == "a" and self._link_stack:
            link_info = self._link_stack.pop()
            href = link_info["href"].strip()
            link_text = link_info["text"].strip() or href
            if href:
                self._parts.append(f"[{link_text}]({href})")
            else:
                self._parts.append(link_text)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._link_stack:
            self._link_stack[-1]["text"] += data
        else:
            self._parts.append(data)


class _TextExtractor(HTMLParser):
    """Strips all HTML tags to extract raw plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignored_depth or tag.lower() in _IGNORED_TAGS:
            return
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)


def normalize_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return normalize_text(parser.text)


def html_to_markdown(value: str) -> str:
    parser = _HtmlToMarkdownParser()
    parser.feed(value)
    parser.close()
    result = html.unescape(parser.markdown)
    return normalize_text(result)


def looks_like_html(value: str) -> bool:
    return bool(_HTML_PATTERN.search(value))


def body_to_markdown(value: str, body_format: str) -> str:
    if body_format == "html" or (body_format == "text" and looks_like_html(value)):
        return html_to_markdown(value)
    return normalize_text(value)


def truncate(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{cut.rstrip(' ,.;:')}..."


def summary_from_body(body: str, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", body).strip()
    return truncate(text, limit)


def clean_summary(value: str, fallback_body: str) -> str:
    text = html_to_text(value) if looks_like_html(value) else normalize_text(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = summary_from_body(fallback_body)
    return truncate(text, 280)


def count_words(value: str) -> int:
    return len(re.findall(r"\S+", value))
