"""Convert 4chan comment HTML into Pango markup."""
import html
import re
from html.parser import HTMLParser

from gi.repository import GLib

_esc = GLib.markup_escape_text
GREEN = "#789922"

_RE_URL = re.compile(r"https?://[^\s<>\"']+")
_RE_SAME = re.compile(r"^#p(\d+)$")
_RE_THREAD = re.compile(r"^(?:https?:)?(?://boards\.4chan(?:nel)?\.org)?/(\w+)/thread/(\d+)(?:#p(\d+))?$")


def map_href(href):
    m = _RE_SAME.match(href)
    if m:
        return f"post:{m.group(1)}"
    m = _RE_THREAD.match(href)
    if m:
        return f"thread:{m.group(1)}/{m.group(2)}/{m.group(3) or ''}"
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://boards.4chan.org" + href
    return href


class _Parser(HTMLParser):
    def __init__(self, op_no):
        super().__init__(convert_charrefs=True)
        self.op_no = op_no
        self.out, self.plain, self.quotes, self.spoilers = [], [], [], []
        self.stack = []  # (tag, closing markup)
        self._in_spoiler = False
        self._link_post = None
        self._buf = []  # text pending linkification; <wbr> does not split it

    def flush(self):
        """Emit buffered text, turning bare http(s) URLs into links."""
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf.clear()
        self.plain.append(text)
        if self._in_spoiler and self.spoilers:
            self.spoilers[-1] += text
        # No new links inside an existing link, or inside a spoiler (the link
        # colour would reveal the hidden text).
        if self._in_spoiler or any(t == "a" for t, _ in self.stack):
            self.out.append(_esc(text))
            return
        pos = 0
        for m in _RE_URL.finditer(text):
            url = m.group(0).rstrip(".,;:!?)]}")
            self.out.append(_esc(text[pos:m.start()]))
            self.out.append(f'<a href="{_esc(url)}">{_esc(url)}</a>')
            pos = m.start() + len(url)
        self.out.append(_esc(text[pos:]))

    def handle_starttag(self, tag, attrs):
        if tag == "wbr":
            return
        self.flush()
        a = dict(attrs)
        cls = a.get("class", "")
        close = ""
        if tag == "br":
            self.out.append("\n")
            self.plain.append("\n")
            return
        if tag == "a":
            href = map_href(a.get("href", ""))
            if href.startswith("post:"):
                self._link_post = int(href[5:])
                self.quotes.append(self._link_post)
            self.out.append(f'<a href="{_esc(href)}">')
            close = "</a>"
        elif tag == "span" and "deadlink" in cls:
            self.out.append('<span foreground="#888888" strikethrough="true">')
            close = "</span>"
        elif tag == "span" and "quote" in cls:
            self.out.append(f'<span foreground="{GREEN}">')
            close = "</span>"
        elif tag == "s":
            self._in_spoiler = True
            self.spoilers.append("")
            self.out.append('<span foreground="#5a5a5a" background="#5a5a5a">')
            close = "</span>"
        elif tag == "pre":
            self.out.append("<tt>")
            close = "</tt>"
        elif tag in ("b", "strong"):
            self.out.append("<b>")
            close = "</b>"
        elif tag in ("i", "em"):
            self.out.append("<i>")
            close = "</i>"
        elif tag == "u":
            self.out.append("<u>")
            close = "</u>"
        self.stack.append((tag, close))

    def handle_endtag(self, tag):
        if tag == "wbr":
            return
        self.flush()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                while len(self.stack) > i:
                    t, close = self.stack.pop()
                    if t == "a" and self._link_post == self.op_no:
                        self.out.append(" (OP)")
                    if t == "s":
                        self._in_spoiler = False
                    self.out.append(close)
                if tag == "a":
                    self._link_post = None
                return

    def handle_data(self, data):
        self._buf.append(data)


def html_to_pango(src, op_no=0):
    """Return (pango_markup, plain_text, quoted_post_numbers, spoiler_texts)."""
    if not src:
        return "", "", [], []
    p = _Parser(op_no)
    p.feed(src)
    p.close()
    p.flush()
    while p.stack:  # close anything left open
        p.out.append(p.stack.pop()[1])
    return "".join(p.out), "".join(p.plain), p.quotes, p.spoilers


def clean(text):
    """Unescape an API text field (name, subject, filename)."""
    return html.unescape(text or "")
