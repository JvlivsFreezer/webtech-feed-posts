import html
import math
import re

import nh3
from markdown_it import MarkdownIt

WORDS_PER_MINUTE = 200

# html=False escapes any raw HTML users type; nh3 below is defense in depth
_md = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(
    ["table", "strikethrough"],
)

ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "th": {"style"},
    "td": {"style"},
}


def render_markdown(text: str) -> str:
    """Render user Markdown to sanitized HTML."""
    return nh3.clean(
        _md.render(text),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    )


def plain_text(text: str) -> str:
    """Markdown source to a single line of plain text."""
    stripped = nh3.clean(_md.render(text), tags=set())
    return re.sub(r"\s+", " ", html.unescape(stripped)).strip()


def excerpt(text: str, length: int) -> str:
    plain = plain_text(text)
    if len(plain) <= length:
        return plain
    # Cut on a word boundary so we don't end mid-word
    cut = plain[:length].rsplit(" ", 1)[0].rstrip(".,;:!?-")
    return f"{cut}…"


def reading_time(text: str) -> int:
    """Estimated minutes to read, never less than 1."""
    words = len(plain_text(text).split())
    return max(1, math.ceil(words / WORDS_PER_MINUTE))
