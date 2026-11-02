"""GObject wrappers around API dicts, so they can live in Gio.ListStore."""
import time

from gi.repository import GLib, GObject

from .markup import clean, html_to_pango
from .net import CACHE, IMG

_esc = GLib.markup_escape_text


def rel_time(t):
    d = max(0, int(time.time()) - int(t))
    if d < 60:
        return "just now"
    for size, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"), (3600, "h"), (60, "m")):
        if d >= size:
            return f"{d // size}{unit} ago"
    return ""


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class Post(GObject.Object):
    def __init__(self, d, board, op_no):
        super().__init__()
        self.d, self.board, self.no, self.op_no = d, board, d["no"], op_no
        self.markup, self.plain, self.quotes, self.spoilers = html_to_pango(d.get("com", ""), op_no)
        self.backlinks = []
        self.highlight = False
        self.tim = d.get("tim")
        self.has_media = bool(self.tim)
        self.ext = d.get("ext", "")
        self.is_video = self.ext in (".webm", ".mp4")
        self.filename = clean(d.get("filename", "")) + self.ext
        self.header_markup = self._header()

    def _header(self):
        d, parts = self.d, []
        if d.get("sub"):
            parts.append(f'<b><span foreground="#4a86e8">{_esc(clean(d["sub"]))}</span></b>')
        name = clean(d.get("name", "Anonymous"))
        parts.append(f'<b><span foreground="#2f9e57">{_esc(name)}</span></b>')
        if d.get("trip"):
            parts.append(f'<span foreground="#2f9e57">{_esc(d["trip"])}</span>')
        if d.get("capcode"):
            parts.append(f'<b><span foreground="#d6403b">## {_esc(d["capcode"].title())}</span></b>')
        if d.get("id"):
            parts.append(f'<span background="#6a6a6a" foreground="#ffffff"> {_esc(d["id"])} </span>')
        if d.get("country_name"):
            parts.append(f'<span alpha="70%">[{_esc(clean(d["country_name"]))}]</span>')
        elif d.get("board_flag") and d.get("flag_name"):
            parts.append(f'<span alpha="70%">[{_esc(clean(d["flag_name"]))}]</span>')
        when = GLib.DateTime.new_from_unix_local(d["time"]).format("%m/%d/%y %H:%M")
        parts.append(f'<span alpha="65%">{when} · {rel_time(d["time"])}</span>')
        parts.append(f'<span alpha="65%">No.{self.no}</span>')
        if self.no == self.op_no:
            parts.append('<span alpha="65%">OP</span>')
        return "  ".join(parts)

    # ---- media -------------------------------------------------------------
    @property
    def thumb_url(self):
        return f"{IMG}/{self.board}/{self.tim}s.jpg"

    @property
    def thumb_path(self):
        return CACHE / "thumbs" / self.board / f"{self.tim}s.jpg"

    @property
    def media_url(self):
        return f"{IMG}/{self.board}/{self.tim}{self.ext}"

    @property
    def media_path(self):
        return CACHE / "media" / self.board / f"{self.tim}{self.ext}"

    @property
    def file_info(self):
        d = self.d
        return f"{self.filename}\n{human_size(d.get('fsize', 0))}, {d.get('w', '?')}×{d.get('h', '?')}"

    @property
    def spoiler_image(self):
        return bool(self.d.get("spoiler"))


class ThreadItem(GObject.Object):
    """One catalog entry."""

    def __init__(self, d, board, index):
        super().__init__()
        self.d, self.board, self.index, self.no = d, board, index, d["no"]
        self.subject = clean(d.get("sub", ""))
        _, self.plain, _, _ = html_to_pango(d.get("com", ""))
        self.text = (self.subject + " " + self.plain).lower()
        self.replies = d.get("replies", 0)
        self.images = d.get("images", 0)
        self.created = d.get("time", 0)
        self.last_modified = d.get("last_modified", self.created)
        self.tim = d.get("tim")

    @property
    def thumb_url(self):
        return f"{IMG}/{self.board}/{self.tim}s.jpg"

    @property
    def thumb_path(self):
        return CACHE / "thumbs" / self.board / f"{self.tim}s.jpg"

    @property
    def has_full_image(self):
        """Videos have no still image on the server, only the thumbnail."""
        return bool(self.tim) and self.d.get("ext") in (".jpg", ".jpeg", ".png", ".gif")

    @property
    def media_url(self):
        return f"{IMG}/{self.board}/{self.tim}{self.d.get('ext', '')}"

    @property
    def media_path(self):
        return CACHE / "media" / self.board / f"{self.tim}{self.d.get('ext', '')}"
