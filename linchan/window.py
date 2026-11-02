"""Main window: boards + watch-list sidebar, navigation stack for content."""
import json
import re
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from . import APP_ID, HOMEPAGE, __version__
from .catalog import CatalogPage
from .net import API, CACHE
from .thread import ThreadPage

_esc = GLib.markup_escape_text
WATCH_POLL_SECS = 90


class Window(Adw.ApplicationWindow):
    def __init__(self, app, net, state):
        super().__init__(application=app, title="Linchan")
        self.net, self.state = net, state
        self.set_default_size(state["width"], state["height"])
        self.boards = []  # list of board dicts
        self._watch_lm = {}  # key -> Last-Modified for background polling

        # --- content
        self.nav = Adw.NavigationView()
        self.split = Adw.OverlaySplitView(content=self.nav, sidebar=self._build_sidebar(), max_sidebar_width=300, min_sidebar_width=230)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        self.set_content(self.split)

        # --- actions
        self._action("open-url", self.prompt_open)
        self._action("refresh", self.refresh_current)
        self._action("back", lambda *_: self.nav.pop())
        self._action("toggle-sidebar", lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()))
        self._action("about", self.show_about)
        nsfw = Gio.SimpleAction.new_stateful("hide-nsfw", None, GLib.Variant.new_boolean(state["hide_nsfw"]))
        nsfw.connect("change-state", self._on_hide_nsfw)
        self.add_action(nsfw)

        self.connect("close-request", self._on_close)
        self._load_boards()
        self._rebuild_watched()
        self.open_board(state["board"])
        GLib.timeout_add_seconds(WATCH_POLL_SECS, self._poll_watched)

    # ---- helpers -------------------------------------------------------------------
    def _action(self, name, fn):
        a = Gio.SimpleAction.new(name, None)
        a.connect("activate", fn)
        self.add_action(a)

    def _on_close(self, *_):
        w, h = self.get_default_size()
        self.state.data.update(width=w, height=h)
        self.state.save()
        return False

    def toast(self, text):
        pass

    # ---- sidebar ---------------------------------------------------------------------
    def _build_sidebar(self):
        menu = Gio.Menu()
        menu.append("Open URL or Board…", "win.open-url")
        sec = Gio.Menu()
        sec.append("Hide NSFW Boards", "win.hide-nsfw")
        menu.append_section(None, sec)
        sec2 = Gio.Menu()
        sec2.append("About Linchan", "win.about")
        sec2.append("Quit", "app.quit")
        menu.append_section(None, sec2)
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Linchan", subtitle=""))
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))

        self.watched_label = Gtk.Label(label="Watched", xalign=0, css_classes=["heading"], margin_start=12, margin_top=8, margin_bottom=4)
        self.watched_list = Gtk.ListBox(css_classes=["navigation-sidebar"], selection_mode=Gtk.SelectionMode.NONE)
        self.watched_list.connect("row-activated", lambda _l, r: self.open_thread(r.board, r.no))
        self.board_search = Gtk.SearchEntry(placeholder_text="Find board…", margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        self.board_search.connect("search-changed", lambda *_: self.board_list.invalidate_filter())
        self.board_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.board_list.set_filter_func(self._board_filter)
        self.board_list.connect("row-activated", lambda _l, r: self.open_board(r.code))
        self.board_status = Gtk.Label(label="Loading boards…", css_classes=["dim-label"], margin_top=12)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for w in (self.watched_label, self.watched_list, Gtk.Label(label="Boards", xalign=0, css_classes=["heading"], margin_start=12, margin_top=8),
                  self.board_search, self.board_status, self.board_list):
            box.append(w)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        view.add_top_bar(header)
        return view

    def _board_filter(self, row):
        if self.state["hide_nsfw"] and not row.ws:
            return False
        q = self.board_search.get_text().strip().lower().strip("/")
        return not q or q in row.code.lower() or q in row.title.lower()

    def _load_boards(self):
        cache = CACHE / "boards.json"
        try:
            self._set_boards(json.loads(cache.read_text())["boards"])
        except (OSError, ValueError, KeyError):
            pass

        def done(res, err):
            if err or res.status != 200:
                if not self.boards:
                    self.board_status.set_label("Could not load boards")
                return
            try:
                data = res.json()
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(res.body)
                self._set_boards(data["boards"])
            except (ValueError, KeyError, OSError):
                pass

        self.net.get(f"{API}/boards.json", done, api=True)

    def _set_boards(self, boards):
        self.boards = boards
        self.board_list.remove_all()
        for b in boards:
            row = Gtk.ListBoxRow()
            row.code, row.title, row.ws = b["board"], b.get("title", ""), bool(b.get("ws_board"))
            lbl = Gtk.Label(use_markup=True, xalign=0, ellipsize=Pango.EllipsizeMode.END, margin_top=4, margin_bottom=4,
                            label=f"<b>/{_esc(row.code)}/</b>  <span alpha='70%'>{_esc(row.title)}</span>")
            row.set_child(lbl)
            self.board_list.append(row)
        self.board_status.set_visible(False)

    def _on_hide_nsfw(self, action, value):
        action.set_state(value)
        self.state["hide_nsfw"] = value.get_boolean()
        self.board_list.invalidate_filter()

    # ---- navigation -----------------------------------------------------------------
    def _board_title(self, code):
        return next((b.get("title", "") for b in self.boards if b["board"] == code), "")

    def open_board(self, code):
        code = code.strip("/").lower()
        self.state["board"] = code
        self.nav.replace([CatalogPage(self, code, self._board_title(code))])
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def open_thread(self, board, no, post=None):
        top = self.nav.get_visible_page()
        if isinstance(top, ThreadPage) and top.board == board and top.no == no:
            if post:
                top.goto_post(post)
            return
        self.nav.push(ThreadPage(self, board, no, focus=post))
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def refresh_current(self, *_):
        page = self.nav.get_visible_page()
        if hasattr(page, "refresh"):
            page.refresh()

    # ---- open-by-URL dialog ------------------------------------------------------------
    def prompt_open(self, *_):
        entry = Gtk.Entry(placeholder_text="g   or   https://boards.4chan.org/g/thread/123456", activates_default=True)
        dlg = Adw.AlertDialog(heading="Open Board or Thread", body="Paste a thread URL, or type a board like g.")
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("open", "Open")
        dlg.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("open")

        def done(d, res):
            if d.choose_finish(res) == "open":
                self.open_target(entry.get_text())

        dlg.choose(self, None, done)

    def open_target(self, text):
        text = text.strip()
        m = re.search(r"(?:^|/)(\w+)/thread/(\d+)(?:#p(\d+))?", text)
        if m:
            return self.open_thread(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        m = re.search(r"4chan(?:nel)?\.org/(\w+)", text) or re.fullmatch(r"/?(\w+)/?", text)
        if m:
            self.open_board(m.group(1))

    def show_about(self, *_):
        Adw.AboutDialog(
            application_name="Linchan",
            application_icon=APP_ID,
            version=__version__,
            developer_name="songanane",
            developers=["songanane"],
            copyright="© 2026 songanane",
            comments="A native GTK4 imageboard browser.\n\nLinchan is an unofficial client and is not affiliated with or endorsed by 4chan.",
            website=HOMEPAGE,
            issue_url=f"{HOMEPAGE}/issues",
            license_type=Gtk.License.GPL_3_0,
        ).present(self)

    # ---- watch list --------------------------------------------------------------------
    def _key(self, board, no):
        return f"{board}/{no}"

    def is_watched(self, board, no):
        return self._key(board, no) in self.state["watched"]

    def watch(self, board, no, subject, replies):
        self.state["watched"][self._key(board, no)] = dict(board=board, no=no, subject=subject, seen=replies, replies=replies, dead=False)
        self.state.save()
        self._rebuild_watched()

    def unwatch(self, board, no):
        self.state["watched"].pop(self._key(board, no), None)
        self.state.save()
        self._rebuild_watched()

    def thread_seen(self, board, no):
        w = self.state["watched"].get(self._key(board, no))
        if w and w["seen"] != w["replies"]:
            w["seen"] = w["replies"]
            self.state.save()
            self._rebuild_watched()

    def thread_update(self, board, no, subject, replies, viewing=False):
        w = self.state["watched"].get(self._key(board, no))
        if not w:
            return
        w.update(subject=subject, replies=replies)
        if viewing:
            w["seen"] = replies
        self.state.save()
        self._rebuild_watched()

    def thread_dead(self, board, no):
        w = self.state["watched"].get(self._key(board, no))
        if w and not w["dead"]:
            w["dead"] = True
            self.state.save()
            self._rebuild_watched()

    def _rebuild_watched(self):
        self.watched_list.remove_all()
        items = list(self.state["watched"].values())
        self.watched_label.set_visible(bool(items))
        self.watched_list.set_visible(bool(items))
        for w in items:
            row = Gtk.ListBoxRow()
            row.board, row.no = w["board"], w["no"]
            box = Gtk.Box(spacing=6, margin_top=4, margin_bottom=4)
            title = Gtk.Label(label=f"/{w['board']}/ {w['subject']}", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            box.append(title)
            unread = w["replies"] - w["seen"]
            if w["dead"]:
                row.add_css_class("dead-thread")
                title.set_tooltip_text("404")
            elif unread > 0:
                box.append(Gtk.Label(label=str(unread), css_classes=["unread-badge"], valign=Gtk.Align.CENTER))
            rm = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"], valign=Gtk.Align.CENTER, tooltip_text="Stop watching")
            rm.connect("clicked", lambda _b, w=w: self.unwatch(w["board"], w["no"]))
            box.append(rm)
            row.set_child(box)
            self.watched_list.append(row)
        # keep a thread page's star in sync
        for page in self.nav.get_navigation_stack():
            if isinstance(page, ThreadPage):
                page._sync_watch_btn()

    def _poll_watched(self):
        """Check every watched thread for new replies, one request per second."""
        visible = self.nav.get_visible_page()
        for key, w in list(self.state["watched"].items()):
            if w["dead"] or (isinstance(visible, ThreadPage) and (visible.board, visible.no) == (w["board"], w["no"])):
                continue
            headers = {"If-Modified-Since": self._watch_lm[key]} if key in self._watch_lm else {}

            def done(res, err, key=key, w=w):
                if err:
                    return
                if res.status == 404:
                    return self.thread_dead(w["board"], w["no"])
                if res.status == 200:
                    self._watch_lm[key] = res.headers.get("Last-Modified", "")
                    if not self._watch_lm[key]:
                        self._watch_lm.pop(key)
                    try:
                        replies = len(res.json()["posts"]) - 1
                    except (ValueError, KeyError):
                        return
                    if replies != w["replies"]:
                        self.thread_update(w["board"], w["no"], w["subject"], replies)

            self.net.get(f"{API}/{w['board']}/thread/{w['no']}.json", done, api=True, headers=headers)
        return True
