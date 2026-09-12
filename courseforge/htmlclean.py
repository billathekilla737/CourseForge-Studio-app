"""Make Canvas rich text safe and self-contained enough to render in this app.

Canvas assignment descriptions are real web pages. A live one from this
instructor's own course carries a `<script src=...s3.amazonaws.com...>` and a
`<link rel="stylesheet">` pointing at an account-wide CSS file. Dropping that
straight into the page would run someone else's script inside the tool and let a
remote stylesheet restyle the whole app, so the HTML goes through an allow list
on the way out.

Three decisions worth knowing about:

* **`class` is dropped, `style` is kept.** A description written against the
  institution's own CSS classes would otherwise collide with this app's
  stylesheet -- a `class="btn"` in a description would pick up the app's button
  styling. Inline styles are what make a description look the way its author
  intended, and they cannot reach outside the element they sit on.
* **Relative links are made absolute.** Canvas writes `/courses/1/files/2` and
  the app is served from localhost, where that path means nothing. Resolving
  against the Canvas host makes images and links work.
* **Anything that loads or runs code is removed, not escaped.** Scripts, styles,
  iframes, objects and form controls go; an iframe is replaced by a plain link so
  an embedded video is still reachable.
"""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin

# Structural and text tags only. Everything else is unwrapped (its text is kept)
# or dropped entirely if it is in DROP_WHOLE.
ALLOWED: dict[str, set[str]] = {
    "a": {"href", "title"},
    "p": {"style"}, "div": {"style"}, "span": {"style"}, "br": set(), "hr": set(),
    "h1": {"style"}, "h2": {"style"}, "h3": {"style"}, "h4": {"style"},
    "h5": {"style"}, "h6": {"style"},
    "ul": {"style"}, "ol": {"style", "start"}, "li": {"style"},
    "strong": {"style"}, "b": {"style"}, "em": {"style"}, "i": {"style"},
    "u": set(), "s": set(), "sup": set(), "sub": set(), "small": set(),
    "blockquote": {"style"}, "code": set(), "pre": {"style"},
    "table": {"style", "border", "cellpadding", "cellspacing"},
    "thead": {"style"}, "tbody": {"style"}, "tfoot": {"style"},
    "tr": {"style"}, "th": {"style", "colspan", "rowspan", "scope"},
    "td": {"style", "colspan", "rowspan"},
    "caption": {"style"},
    "img": {"src", "alt", "width", "height", "style", "title"},
    "figure": {"style"}, "figcaption": {"style"},
    "dl": set(), "dt": set(), "dd": set(),
}

VOID = {"br", "hr", "img"}

# Content that must not survive at all, text included. These have closing tags,
# so everything up to the matching close is skipped.
DROP_WHOLE = {"script", "style", "noscript", "svg", "math", "object", "applet",
              "form", "select", "textarea", "button", "template"}

# The same intent, but for void elements. These never close, so they must drop
# themselves and nothing more: treating <link> as a container swallowed every
# real description, because Canvas opens with a stylesheet link and the "skip
# until the close tag" state then never ended.
DROP_VOID = {"link", "meta", "base", "input", "param", "track", "area", "col",
             "wbr", "keygen"}

# Replaced by a link to whatever it was embedding, so a video stays reachable.
EMBED = {"iframe", "video", "audio", "source"}

SAFE_SCHEME = re.compile(r"^(?:https?:|mailto:|tel:|/|#|\./|\.\./)", re.I)
# url() inside a style attribute can fetch a remote asset; strip those and any
# expression()/behaviour hacks rather than trying to parse CSS.
BAD_CSS = re.compile(r"(?:url\s*\(|expression\s*\(|@import|behaviou?r\s*:)", re.I)


def _safe_url(value: str, base: str) -> str | None:
    value = (value or "").strip().replace("\x00", "")
    if not value or not SAFE_SCHEME.match(value):
        return None                     # javascript:, data:, vbscript:, ...
    if base and not re.match(r"^(?:https?:|mailto:|tel:|#)", value, re.I):
        return urljoin(base.rstrip("/") + "/", value.lstrip("/"))
    return value


def _safe_style(value: str) -> str | None:
    if not value or BAD_CSS.search(value):
        return None
    return value if len(value) <= 400 else None


class _Cleaner(HTMLParser):
    def __init__(self, base: str = ""):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.dropping = 0               # depth inside a DROP_WHOLE element
        self.open_tags: list[str] = []

    # -------------------------------------------------------------- handlers
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_VOID:
            return                      # never opens a region: drop just this
        if self.dropping:
            if tag in DROP_WHOLE:
                self.dropping += 1
            return
        if tag in DROP_WHOLE:
            self.dropping = 1
            return
        if tag in EMBED:
            href = _safe_url(dict(attrs).get("src") or "", self.base)
            if href:
                self.out.append(
                    f'<p><a href="{escape(href, quote=True)}" target="_blank" '
                    'rel="noopener noreferrer">Embedded media (opens in a new '
                    "tab)</a></p>")
            return
        if tag not in ALLOWED:
            return                      # unwrap: keep the text, lose the tag
        kept = []
        for name, value in attrs:
            name = (name or "").lower()
            if name.startswith("on") or name not in ALLOWED[tag]:
                continue
            if name in ("href", "src"):
                value = _safe_url(value or "", self.base)
                if not value:
                    continue
            elif name == "style":
                value = _safe_style(value or "")
                if not value:
                    continue
            kept.append(f'{name}="{escape(str(value), quote=True)}"')
        if tag == "a":
            kept.append('target="_blank"')
            kept.append('rel="noopener noreferrer"')
        if tag == "img":
            kept.append('loading="lazy"')
        attrs_text = (" " + " ".join(kept)) if kept else ""
        if tag in VOID:
            self.out.append(f"<{tag}{attrs_text}>")
        else:
            self.out.append(f"<{tag}{attrs_text}>")
            self.open_tags.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.dropping:
            if tag in DROP_WHOLE:
                self.dropping -= 1
            return
        if tag in VOID or tag in EMBED or tag not in ALLOWED:
            return
        if tag in self.open_tags:
            # Close anything left open inside it, so a stray </div> cannot
            # unbalance the page around it.
            while self.open_tags:
                current = self.open_tags.pop()
                self.out.append(f"</{current}>")
                if current == tag:
                    break

    def handle_data(self, data):
        if not self.dropping:
            self.out.append(escape(data))

    def close(self):
        super().close()
        while self.open_tags:
            self.out.append(f"</{self.open_tags.pop()}>")


def clean(html: str, base_url: str = "") -> str:
    """Sanitised HTML, safe to insert into the app's own page."""
    if not html or not html.strip():
        return ""
    cleaner = _Cleaner(base_url)
    cleaner.feed(html)
    cleaner.close()
    text = "".join(cleaner.out)
    # Collapse the runs of blank markup Canvas's editor leaves behind.
    text = re.sub(r"(?:<p>\s*</p>\s*)+", "", text)
    return text.strip()


def looks_empty(html: str) -> bool:
    """True when there is nothing a person would call content."""
    return not re.sub(r"<[^>]+>|&nbsp;|\s", "", html or "")
