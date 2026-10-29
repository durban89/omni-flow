from __future__ import annotations

import re
from html.parser import HTMLParser

_IGNORED_TAGS = {"iframe", "noscript", "script", "style", "svg"}
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_HTML_PATTERN = re.compile(r"<\s*[a-zA-Z][^>]*>")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignored_depth or tag in _IGNORED_TAGS:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag in _BLOCK_TAGS:
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


def looks_like_html(value: str) -> bool:
    return bool(_HTML_PATTERN.search(value))


def body_to_markdown(value: str, body_format: str) -> str:
    if body_format == "html" or (body_format == "text" and looks_like_html(value)):
        return html_to_text(value)
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
