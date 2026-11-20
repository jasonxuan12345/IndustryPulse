"""Select a sourced excerpt from an SEC 8-K or its referenced exhibits."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

_DOCUMENTS = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.IGNORECASE | re.DOTALL)
_EXHIBIT = re.compile(r"\b(?:exhibits?|ex[- ])\s*(\d+\.\d+)\b", re.IGNORECASE)
_ACTION = re.compile(
    r"\b(?:entered into|signed (?:a|an|the)|agreed to (?:acquire|sell)|"
    r"completed (?:the|its) (?:acquisition|merger|sale)|appointed|resigned|will step down|"
    r"terminated|declared (?:a|its) dividend|approved .*repurchase)\b",
    re.IGNORECASE,
)
_METRIC = re.compile(
    r"\b(?:revenue|net sales|net income|net loss|gross margin|earnings per share|"
    r"operating income|cash flow|capital expenditures|guidance)\b",
    re.IGNORECASE,
)
_AMOUNT = re.compile(r"\$\s*\d|\b\d+(?:\.\d+)?\s*(?:%|million|billion|percent)\b|\d\s*%")
_BOILERPLATE = re.compile(
    r"^\s*(?:item\s*\d\.\d\d|pursuant to|forward.looking|the foregoing|"
    r"the information (?:reported|in this|contained|presented|furnished)|"
    r"copy of (?:the|this)|press release (?:dated|issued by))",
    re.IGNORECASE,
)
_NON_BODY = {"script", "style", "head", "ix:hidden", "h1", "h2", "h3", "h4", "h5", "h6"}


class _Text(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.links: list[str] = []
        self.hidden = 0
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _NON_BODY:
            self.hidden += 1
        if self.hidden:
            return
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.chunks.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(urlsplit(href).path.rsplit("/", 1)[-1])

    def handle_endtag(self, tag: str) -> None:
        if tag in _NON_BODY:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.chunks.append(data)

    def paragraphs(self) -> list[str]:
        return [
            " ".join(line.split()) for line in "".join(self.chunks).splitlines() if line.strip()
        ]


def _quote(lines: list[str]) -> str | None:
    for line in lines:
        if len(line) < 55 or _BOILERPLATE.match(line):
            continue
        match = _ACTION.search(line) or (_METRIC.search(line) if _AMOUNT.search(line) else None)
        if match:
            # Keep the section containing the business fact in a long paragraph.
            start = max(0, match.start() - 100)
            return line[start : start + 420].strip()
    return None


def extract_8k_excerpt(submission: str) -> tuple[str, str] | None:
    """Prefer exhibits named by the main document, then the first EX-99.x, then the body."""
    main: _Text | None = None
    main_name = ""
    exhibits: list[tuple[str, str, str]] = []
    for doc in _DOCUMENTS.findall(submission):
        kind = re.search(r"^\s*<TYPE>\s*([^\r\n<]+)", doc, re.MULTILINE | re.IGNORECASE)
        filename = re.search(r"^\s*<FILENAME>\s*([\w.-]+)", doc, re.MULTILINE | re.IGNORECASE)
        text = re.search(r"<TEXT>(.*?)</TEXT>", doc, re.IGNORECASE | re.DOTALL)
        if not kind or not filename or not text:
            continue
        form = kind[1].strip().upper()
        if form in {"8-K", "8-K/A"} and main is None:
            main, main_name = _Text(text[1]), filename[1]
        elif form.startswith("EX-") and filename[1].lower().endswith((".htm", ".html", ".txt")):
            exhibits.append((form, filename[1], text[1]))
    lines = main.paragraphs() if main else []
    references = list(dict.fromkeys(_EXHIBIT.findall(" ".join(lines))))
    links = {link.casefold() for link in main.links} if main else set()
    selected = [
        (name, body)
        for reference in references
        for form, name, body in exhibits
        if form.removeprefix("EX-") == reference
    ]
    selected.extend(
        (name, body)
        for _, name, body in exhibits
        if name.casefold() in links and (name, body) not in selected
    )
    fallback = next(
        ((name, body) for form, name, body in exhibits if form.startswith("EX-99.")), None
    )
    if fallback and fallback not in selected:
        selected.append(fallback)
    for name, body in selected:
        snippet = _quote(_Text(body).paragraphs())
        if snippet:
            return name, snippet
    snippet = _quote(lines)
    return (main_name, snippet) if snippet else None
