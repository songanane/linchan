"""Thread page: virtualised list of posts with live refresh."""

import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .models import Post
from .net import API, WEB, get_texture
from .viewer import Viewer

try:  # posting needs WebKitGTK; browsing keeps working without it
    from .poster import ReplyDialog
except (ImportError, ValueError):
    ReplyDialog = None

REFRESH_SECS = 15
PREVIEW_WIDTH = 520  # px, hover preview of a quoted post


class Thumb(Gtk.Picture):
    """A picture whose size is exactly what we set. A plain Gtk.Picture derives its
    natural height from the texture's aspect ratio and the width it is offered, so
    in a wide column it grew tall and left empty space under the thumbnail."""

    def __init__(self):
        super().__init__(can_shrink=True, content_fit=Gtk.ContentFit.FILL)
        self._w = self._h = 100

    def set_box(self, w, h):
        self._w, self._h = int(w), int(h)
        self.queue_resize()

    def do_measure(self, orientation, for_size):
        size = self._w if orientation == Gtk.Orientation.HORIZONTAL else self._h
        return size, size, -1, -1


class PostRow(Gtk.Box):
    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["post-row"])
        self.page, self.post, self.tok = page, None, 0
        wrap = dict(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0, hexpand=True)
        self.header = Gtk.Label(use_markup=True, **wrap)
        self.picture = Thumb()
        self.thumb_btn = Gtk.Button(child=self.picture, css_classes=["flat", "post-thumb"], halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.thumb_btn.connect("clicked", lambda *_: self.page.open_media(self.post))
        # Natural width is one character; bind() sets the minimum to the thumbnail's
        # width, so the caption is exactly as wide as the picture and never pushes
        # the comment to the right.
        # Filename and size are separate labels so the gaps picture -> name -> size
        # are all the same (a two-line label would add its own line spacing).
        self.fname, self.fsize = (
            Gtk.Label(css_classes=["caption", "dim-label"], xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, width_chars=1)
            for _ in range(2)
        )
        self.thumb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.START)
        self.thumb_btn.set_margin_bottom(1)  # evens out ink gaps: the filename's glyphs start slightly lower
        self.thumb_box.append(self.thumb_btn)
        self.thumb_box.append(self.fname)
        self.thumb_box.append(self.fsize)
        self.comment = Gtk.Label(use_markup=True, selectable=True, yalign=0, **wrap)
        self.comment.connect("activate-link", lambda _l, uri: self.page.on_link(uri))
        body = Gtk.Box(spacing=12)
        body.append(self.thumb_box)
        body.append(self.comment)
        self.backs = Gtk.Label(use_markup=True, css_classes=["caption"], **wrap)
        self.backs.connect("activate-link", lambda _l, uri: self.page.on_link(uri))
        self.reply_btn = Gtk.Button(
            icon_name="mail-reply-sender-symbolic",
            css_classes=["flat", "circular"],
            valign=Gtk.Align.START,
            tooltip_text="Reply to this post",
        )
        self.reply_btn.connect("clicked", lambda *_: self.page.open_reply(self.post.no) if self.post else None)
        head = Gtk.Box(spacing=6)
        head.append(self.header)
        head.append(self.reply_btn)
        for w in (head, body, self.backs):
            self.append(w)
        for lbl in (self.comment, self.backs):  # hover over a >>link to preview the post
            motion = Gtk.EventControllerMotion()
            motion.connect("motion", self._on_motion, lbl)
            motion.connect("leave", lambda *_: self.page.hide_preview())
            lbl.add_controller(motion)

    def _on_motion(self, _ctl, x, y, label):
        uri = label.get_current_uri()
        if uri and uri.startswith("post:"):
            self.page.schedule_preview(label, int(uri[5:]), x, y)
        else:
            self.page.hide_preview()

    def _caption_width(self, w):
        self.fname.set_size_request(w, -1)
        self.fsize.set_size_request(w, -1)

    def bind(self, post):
        self.post, self.tok = post, self.tok + 1
        self.header.set_markup(post.header_markup)
        self.reply_btn.set_visible(self.page.can_reply)
        self.comment.set_markup(post.markup)
        self.comment.set_visible(bool(post.markup))
        self.comment.set_tooltip_text(("Spoiler: " + " / ".join(post.spoilers)) if post.spoilers else None)
        (self.add_css_class if post.highlight else self.remove_css_class)("post-highlight")
        if post.backlinks:
            links = " ".join(f'<a href="post:{n}">&gt;&gt;{n}</a>' for n in post.backlinks)
            self.backs.set_markup(f'<span alpha="70%">Replies:</span> {links}')
        self.backs.set_visible(bool(post.backlinks))
        self.thumb_box.set_visible(post.has_media)
        if not post.has_media:
            return
        name, _, info = post.file_info.partition("\n")
        self.fname.set_label(name)
        self.fsize.set_label(info)
        self.fname.set_tooltip_text(name)
        if post.spoiler_image:
            icon = Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).lookup_icon(
                "view-conceal-symbolic", None, 64, self.get_scale_factor(), Gtk.TextDirection.NONE, 0
            )
            self.picture.set_content_fit(Gtk.ContentFit.SCALE_DOWN)
            self.picture.set_box(100, 100)
            self._caption_width(100)
            self.picture.set_paintable(icon)
            return
        self.picture.set_content_fit(Gtk.ContentFit.FILL)
        tw, th = post.d.get("tn_w", 125), post.d.get("tn_h", 125)
        self.picture.set_box(tw, th)
        self._caption_width(tw)
        self.picture.set_paintable(None)
        tok = self.tok
        get_texture(
            self.page.win.net,
            post.thumb_url,
            post.thumb_path,
            lambda tex: self.picture.set_paintable(tex) if tok == self.tok and tex else None,
        )

    def unbind(self):
        self.tok += 1
        self.post = None
        self.page.hide_preview()


class ThreadPage(Adw.NavigationPage):
    def __init__(self, win, board, no, focus=None):
        super().__init__(title=f"/{board}/ · {no}", tag=f"thread-{board}-{no}")
        self.win, self.board, self.no = win, board, int(no)
        self.store = Gio.ListStore(item_type=Post)
        self.index = {}  # post no -> position
        self.last_modified, self.loading, self.dead, self.timer = None, False, False, 0
        self.pending_focus = int(focus) if focus else None
        self.subject = ""
        self.first_load = True
        self._previewing, self._preview_key, self._preview_src = False, None, 0
        self.closed = False
        self._goto_after, self._goto_tries = None, 0

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(PostRow(self)))
        factory.connect("bind", lambda _f, li: li.get_child().bind(li.get_item()))
        factory.connect("unbind", lambda _f, li: li.get_child().unbind())
        self.list = Gtk.ListView(model=Gtk.NoSelection(model=self.store), factory=factory, css_classes=["post-list"])
        self.scroller = Gtk.ScrolledWindow(
            vexpand=True, child=Adw.ClampScrollable(maximum_size=1000, tightening_threshold=700, child=self.list)
        )
        self.toasts = Adw.ToastOverlay(child=self.scroller)
        # Floating card used for hover previews of quoted posts.
        self.preview_box = Gtk.Box(
            css_classes=["preview-card"], halign=Gtk.Align.START, valign=Gtk.Align.START, can_target=False, visible=False
        )
        self.overlay = Gtk.Overlay(child=self.toasts)
        self.overlay.add_overlay(self.preview_box)
        self.banner = Adw.Banner(revealed=False)
        self.spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)

        self.watch_btn = Gtk.ToggleButton(icon_name="non-starred-symbolic", tooltip_text="Watch thread")
        self.watch_btn.connect("toggled", self._on_watch)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: self.refresh())
        menu = Gio.Menu()
        menu.append("Download all media", "thread.download")
        menu.append("Open in browser", "thread.browser")
        menu.append("Copy link", "thread.copy")
        menu.append("Jump to top", "thread.top")
        menu.append("Jump to bottom", "thread.bottom")
        mb = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, primary=False)
        self.title_widget = Adw.WindowTitle(title=f"/{board}/ · {no}", subtitle="Loading…")
        header = Adw.HeaderBar(title_widget=self.title_widget)
        header.pack_end(mb)
        header.pack_end(refresh)
        header.pack_end(self.watch_btn)
        self.thread_reply_btn = Gtk.Button(
            icon_name="mail-reply-sender-symbolic", tooltip_text="Reply to thread", visible=ReplyDialog is not None
        )
        self.thread_reply_btn.connect("clicked", lambda *_: self.open_reply())
        header.pack_end(self.thread_reply_btn)
        view = Adw.ToolbarView(content=self.overlay)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.set_child(view)

        group = Gio.SimpleActionGroup()
        for name, fn in (
            ("download", self.download_all),
            ("browser", self.open_browser),
            ("copy", self.copy_link),
            ("top", lambda *_: self.scroll_edge(False)),
            ("bottom", lambda *_: self.scroll_edge(True)),
        ):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, fn=fn: fn())
            group.add_action(a)
        self.insert_action_group("thread", group)

        self.connect("shown", self._shown)
        self.connect("hiding", self._hiding)
        self._sync_watch_btn()
        self.refresh()

    # ---- lifecycle -------------------------------------------------------------
    def _shown(self, *_):
        if not self.timer and not self.dead:
            self.timer = GLib.timeout_add_seconds(REFRESH_SECS, self._tick)
        self.win.thread_seen(self.board, self.no)

    def _hiding(self, *_):
        self.hide_preview()
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def _tick(self):
        if self.dead:
            self.timer = 0
            return False
        self.refresh()
        return True

    # ---- fetching ----------------------------------------------------------------
    def refresh(self):
        if self.loading or self.dead:
            return
        self.loading = True
        headers = {"If-Modified-Since": self.last_modified} if self.last_modified else {}
        self.win.net.get(f"{API}/{self.board}/thread/{self.no}.json", self._on_data, api=True, headers=headers)

    def _on_data(self, res, err):
        self.loading = False
        if err:
            self.banner.set_title(f"Network error: {err}")
            self.banner.set_revealed(True)
            return
        if res.status == 304:
            return
        if res.status == 404:
            self.dead = True
            self.banner.set_title("This thread has 404'd — showing the last copy loaded." if len(self.store) else "Thread not found (404).")
            self.banner.set_revealed(True)
            self.win.thread_dead(self.board, self.no)
            self.title_widget.set_subtitle("404")
            return
        if res.status != 200:
            self.banner.set_title(f"Server returned HTTP {res.status}")
            self.banner.set_revealed(True)
            return
        self.banner.set_revealed(False)
        self.last_modified = res.headers.get("Last-Modified")
        try:
            self._merge(res.json()["posts"])
        except (ValueError, KeyError) as e:
            self.banner.set_title(f"Bad response: {e}")
            self.banner.set_revealed(True)

    def _merge(self, posts):
        adj = self.scroller.get_vadjustment()
        at_bottom = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 120
        op = posts[0]
        self.closed = bool(op.get("closed") or op.get("archived"))
        self.thread_reply_btn.set_sensitive(self.can_reply)
        new = [Post(d, self.board, self.no) for d in posts if d["no"] not in self.index]
        start = len(self.store)
        for i, p in enumerate(new):
            self.index[p.no] = start + i
        self.store.splice(start, 0, new)
        changed = set()
        for p in new:  # backlinks
            for q in p.quotes:
                pos = self.index.get(q)
                if pos is None:
                    continue
                target = self.store.get_item(pos)
                if p.no not in target.backlinks:
                    target.backlinks.append(p.no)
                    changed.add(pos)
        for pos in changed:
            if pos < start:
                self.store.items_changed(pos, 1, 1)
        # header
        from .markup import clean, html_to_pango

        self.subject = clean(op.get("sub", "")) or html_to_pango(op.get("com", ""))[1][:60].replace("\n", " ") or f"Thread {self.no}"
        bits = [f"{op.get('replies', len(posts) - 1)} replies", f"{op.get('images', 0)} images"]
        for key, label in (("bumplimit", "bump limit"), ("imagelimit", "image limit"), ("closed", "closed"), ("archived", "archived")):
            if op.get(key):
                bits.append(label)
        self.title_widget.set_title(self.subject)
        self.title_widget.set_subtitle(f"/{self.board}/ · " + " · ".join(bits))
        if op.get("archived"):
            self.dead = True
            self.banner.set_title("This thread is archived.")
            self.banner.set_revealed(True)
        self.win.thread_update(self.board, self.no, self.subject, len(posts) - 1, viewing=True)
        if new and not self.first_load:
            self.toasts.add_toast(Adw.Toast(title=f"{len(new)} new post{'s' if len(new) != 1 else ''}", timeout=2))
            if at_bottom:
                GLib.idle_add(lambda: self.scroll_edge(True) and False)
        if self.first_load and self.pending_focus:
            GLib.idle_add(self.goto_post, self.pending_focus)
        self.first_load = False

    # ---- navigation inside thread --------------------------------------------------
    def scroll_edge(self, bottom):
        n = len(self.store)
        if n:
            self.list.scroll_to(n - 1 if bottom else 0, Gtk.ListScrollFlags.NONE, None)
        return False

    def goto_post(self, no):
        pos = self.index.get(no)
        if pos is None:
            self.toasts.add_toast(Adw.Toast(title=f"Post {no} is not in this thread", timeout=2))
            return False
        post = self.store.get_item(pos)
        self.list.scroll_to(pos, Gtk.ListScrollFlags.NONE, None)
        post.highlight = True
        self.store.items_changed(pos, 1, 1)

        def clear():
            post.highlight = False
            p = self.index.get(no)
            if p is not None:
                self.store.items_changed(p, 1, 1)
            return False

        GLib.timeout_add(1800, clear)
        return False

    # ---- hover preview of quoted posts ---------------------------------------------
    def schedule_preview(self, label, no, x, y):
        key = (label, no)
        if key == self._preview_key:
            return  # already showing, or about to show, this link's preview
        self.hide_preview()
        self._preview_key = key
        self._preview_src = GLib.timeout_add(180, self._show_preview, label, no, x, y)

    def _show_preview(self, label, no, x, y):
        """Float the quoted post over the thread. This is an in-window overlay on
        purpose: a Gtk.Popover is a separate Wayland surface, and the compositor
        dismissed it as soon as it opened."""
        self._preview_src = 0
        pos = self.index.get(no)
        if pos is None or not label.get_mapped():
            return False
        pt = label.translate_coordinates(self.overlay, x, y)
        if pt is None:
            return False
        px, py = pt
        row = PostRow(self)
        row.bind(self.store.get_item(pos))
        box = self.preview_box
        width = min(PREVIEW_WIDTH, max(260, self.overlay.get_width() - 16))
        # The clamp caps the width; labels' natural width would otherwise stretch the card.
        box.append(Adw.Clamp(maximum_size=width, tightening_threshold=width, child=row))
        self._preview_at = (int(px), int(py), width)
        self._place_preview(box.measure(Gtk.Orientation.VERTICAL, width)[1])
        box.set_visible(True)
        self._previewing = True
        # The first estimate can be a little short; fix it once the card has its real size.
        GLib.idle_add(self._fix_preview_placement)
        return False

    def _place_preview(self, height):
        px, py, width = self._preview_at
        ow, oh = self.overlay.get_width(), self.overlay.get_height()
        top = py + 24  # below the pointer…
        if top + height > oh - 8:  # …or above it when there is no room
            top = max(8, py - height - 12)
        self.preview_box.set_margin_start(max(8, min(px - 24, ow - width - 8)))
        self.preview_box.set_margin_top(top)

    def _fix_preview_placement(self):
        if self._previewing:
            self._place_preview(self.preview_box.get_height())
        return False

    def hide_preview(self):
        if self._preview_src:
            GLib.source_remove(self._preview_src)
            self._preview_src = 0
        self._preview_key = None
        if self._previewing:
            self._previewing = False
            self.preview_box.set_visible(False)
            child = self.preview_box.get_first_child()
            if child:
                self.preview_box.remove(child)

    def on_link(self, uri):
        self.hide_preview()
        if uri.startswith("post:"):
            self.goto_post(int(uri[5:]))
        elif uri.startswith("thread:"):
            board, no, post = (uri[7:].split("/") + [""])[:3]
            self.win.open_thread(board, int(no), int(post) if post else None)
        else:
            Gtk.UriLauncher(uri=uri).launch(self.win, None, None)
        return True

    # ---- media ---------------------------------------------------------------------
    def media_posts(self):
        return [self.store.get_item(i) for i in range(len(self.store)) if self.store.get_item(i).has_media]

    def open_media(self, post):
        media = self.media_posts()
        if post in media:
            Viewer(self.win, self.win.net, media, media.index(post), on_goto=self.goto_post).present()

    def download_all(self):
        media = self.media_posts()
        if not media:
            return
        # Ask where to save via the file-chooser portal, so no broad filesystem
        # permission is needed when sandboxed.
        dlg = Gtk.FileDialog(title="Save thread media to…", accept_label="Save Here")

        def chosen(d, res):
            try:
                parent = d.select_folder_finish(res)
            except GLib.Error:
                return  # cancelled
            self._download_to(media, Path(parent.get_path()) / f"{self.board}-{self.no}")

        dlg.select_folder(self.win, None, chosen)

    def _download_to(self, media, folder):
        state = {"left": len(media), "ok": 0}
        self.toasts.add_toast(Adw.Toast(title=f"Downloading {len(media)} files…", timeout=2))

        def one(p):
            safe = re.sub(r"[^\w.\- ]", "_", p.filename)
            dest = folder / f"{p.tim}_{safe}"

            def done(path, err):
                state["left"] -= 1
                state["ok"] += 0 if err else 1
                if state["left"] == 0:
                    t = Adw.Toast(title=f"Saved {state['ok']}/{len(media)} files", button_label="Open Folder", timeout=6)
                    t.connect(
                        "button-clicked", lambda *_: Gtk.FileLauncher(file=Gio.File.new_for_path(str(folder))).launch(self.win, None, None)
                    )
                    self.toasts.add_toast(t)

            self.win.net.download(p.media_url, dest, done)

        for p in media:
            one(p)

    # ---- replying ------------------------------------------------------------------------
    @property
    def can_reply(self):
        return ReplyDialog is not None and not self.dead and not self.closed

    def open_reply(self, quote=None):
        """Open the reply dialog, optionally pre-filled with a >>quote of a post."""
        if ReplyDialog is None:
            self.toasts.add_toast(Adw.Toast(title="Posting needs WebKitGTK, which is not installed", timeout=3))
        elif not self.can_reply:
            self.toasts.add_toast(Adw.Toast(title="This thread is closed", timeout=2))
        else:
            ReplyDialog(self.win, self.board, self.no, prefill=f">>{quote}\n" if quote else "", on_posted=self._after_post).present(
                self.win
            )

    def _after_post(self, pid):
        """The post is live: reload the thread until it shows up, then scroll to it."""
        self.toasts.add_toast(Adw.Toast(title="Posted", timeout=2))
        self._goto_after, self._goto_tries = int(pid), 8
        GLib.timeout_add(1500, self._find_new_post)

    def _find_new_post(self):
        if self._goto_after in self.index:
            pid, self._goto_after = self._goto_after, None
            GLib.idle_add(self.goto_post, pid)
            return False
        self._goto_tries -= 1
        if self._goto_tries <= 0:
            return False
        self.last_modified = None  # force a full fetch instead of a 304
        self.refresh()
        return True

    # ---- misc actions ------------------------------------------------------------------
    @property
    def url(self):
        return f"{WEB}/{self.board}/thread/{self.no}"

    def open_browser(self):
        Gtk.UriLauncher(uri=self.url).launch(self.win, None, None)

    def copy_link(self):
        self.get_display().get_clipboard().set(self.url)
        self.toasts.add_toast(Adw.Toast(title="Link copied", timeout=1))

    def _sync_watch_btn(self):
        self._syncing = True
        on = self.win.is_watched(self.board, self.no)
        self.watch_btn.set_active(on)
        self.watch_btn.set_icon_name("starred-symbolic" if on else "non-starred-symbolic")
        self._syncing = False

    def _on_watch(self, btn):
        if getattr(self, "_syncing", False):
            return
        btn.set_icon_name("starred-symbolic" if btn.get_active() else "non-starred-symbolic")
        if btn.get_active():
            self.win.watch(self.board, self.no, self.subject or f"Thread {self.no}", len(self.store) - 1)
        else:
            self.win.unwatch(self.board, self.no)
