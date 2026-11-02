"""Catalog page: searchable, sortable grid of threads."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from .models import ThreadItem
from .net import API, get_scaled_texture, get_texture

FULL_SIZE = 640  # longest side, in px, of the sharp card image

SORTS = ["Bump order", "Last reply", "Most replies", "Most images", "Newest", "Oldest"]
_KEYS = [
    lambda a, b: a.index - b.index,
    lambda a, b: b.last_modified - a.last_modified,
    lambda a, b: b.replies - a.replies,
    lambda a, b: b.images - a.images,
    lambda a, b: b.created - a.created,
    lambda a, b: a.created - b.created,
]


class Card(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["catalog-card", "card"], width_request=300, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self.tok, self.item = 0, None
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, height_request=240, css_classes=["catalog-thumb"])
        self.subject = Gtk.Label(xalign=0, css_classes=["heading"], ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.teaser = Gtk.Label(xalign=0, yalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2, ellipsize=Pango.EllipsizeMode.END, vexpand=False, css_classes=["caption"])
        self.meta = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"])
        for w in (self.picture, self.subject, self.teaser, self.meta):
            self.append(w)

    def bind(self, it):
        self.item, self.tok = it, self.tok + 1
        d = it.d
        self.subject.set_label(it.subject or " ")
        self.subject.set_visible(bool(it.subject))
        self.teaser.set_label(" ".join(it.plain.split())[:300])  # one paragraph so lines=2 applies
        flags = ("📌 " if d.get("sticky") else "") + ("🔒 " if d.get("closed") else "")
        total_images = it.images + (1 if it.tim else 0)  # the API's count excludes the OP's own image
        self.meta.set_label(f"{flags}R: {it.replies} / I: {it.images} / IMG: {total_images}")
        self.set_tooltip_text((it.subject + "\n" if it.subject else "") + it.plain[:600])
        self.picture.set_paintable(None)
        if it.tim:
            tok = self.tok
            have_full = []  # set once the sharp version is shown

            def show_thumb(tex):  # low-res placeholder, never overwrites the sharp one
                if tok == self.tok and tex and not have_full:
                    self.picture.set_paintable(tex)

            def show_full(tex):
                if tok == self.tok and tex:
                    have_full.append(1)
                    self.picture.set_paintable(tex)

            get_texture(self.net, it.thumb_url, it.thumb_path, show_thumb)
            if it.has_full_image:
                get_scaled_texture(self.net, it.media_url, it.media_path, FULL_SIZE, show_full)

    net = None  # set by the page


class CatalogPage(Adw.NavigationPage):
    def __init__(self, win, board, title):
        super().__init__(title=f"/{board}/", tag=f"catalog-{board}")
        self.win, self.board = win, board
        self.store = Gio.ListStore(item_type=ThreadItem)
        self.filter = Gtk.CustomFilter.new(self._match)
        self.sorter = Gtk.CustomSorter.new(lambda a, b, *_: _KEYS[self.sort_idx](a, b))
        self.sort_idx = 0
        model = Gtk.SortListModel(model=Gtk.FilterListModel(model=self.store, filter=self.filter), sorter=self.sorter)
        self.model = model

        factory = Gtk.SignalListItemFactory()

        def setup(_f, li):
            c = Card()
            c.net = win.net
            li.set_child(c)

        factory.connect("setup", setup)
        factory.connect("bind", lambda _f, li: li.get_child().bind(li.get_item()))
        self.grid = Gtk.GridView(model=Gtk.NoSelection(model=model), factory=factory, single_click_activate=True, min_columns=1, max_columns=10, css_classes=["catalog-grid"])
        self.grid.connect("activate", lambda _g, pos: win.open_thread(board, self.model.get_item(pos).no))
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.grid)

        self.search = Gtk.SearchEntry(placeholder_text="Filter threads…", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.filter.changed(Gtk.FilterChange.DIFFERENT))
        self.searchbar = Gtk.SearchBar(child=self.search, show_close_button=True)
        self.searchbar.connect_entry(self.search)
        self.searchbar.set_key_capture_widget(self)

        self.status = Adw.StatusPage(title="Could not load catalog", icon_name="network-error-symbolic")
        retry = Gtk.Button(label="Retry", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        retry.connect("clicked", lambda *_: self.refresh())
        self.status.set_child(retry)
        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32), "loading")
        self.stack.add_named(scroller, "grid")
        self.stack.add_named(self.status, "error")

        # header
        self.title_widget = Adw.WindowTitle(title=f"/{board}/", subtitle=title)
        header = Adw.HeaderBar(title_widget=self.title_widget)
        side = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Boards (F9)")
        win.split.bind_property("show-sidebar", side, "active", GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(side)
        sbtn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Filter (Ctrl+F)")
        sbtn.bind_property("active", self.searchbar, "search-mode-enabled", GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: self.refresh())
        sort = Gtk.DropDown(model=Gtk.StringList.new(SORTS), tooltip_text="Sort")
        sort.connect("notify::selected", self._sort_changed)
        header.pack_end(sbtn)
        header.pack_end(refresh)
        header.pack_end(sort)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.searchbar)
        self.stack.set_vexpand(True)
        content.append(self.stack)
        view = Adw.ToolbarView(content=content)
        view.add_top_bar(header)
        self.set_child(view)
        self.loading = False
        self.refresh()

    def _match(self, item, *_):
        q = self.search.get_text().strip().lower()
        return not q or all(w in item.text for w in q.split())

    def _sort_changed(self, dd, _p):
        self.sort_idx = dd.get_selected()
        self.sorter.changed(Gtk.SorterChange.DIFFERENT)

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        if not len(self.store):
            self.stack.set_visible_child_name("loading")
        self.win.net.get(f"{API}/{self.board}/catalog.json", self._on_data, api=True)

    def _on_data(self, res, err):
        self.loading = False
        if err or res.status != 200:
            if not len(self.store):
                self.status.set_description(str(err) if err else f"HTTP {res.status}")
                self.stack.set_visible_child_name("error")
            return
        try:
            threads = [t for page in res.json() for t in page["threads"]]
        except (ValueError, KeyError, TypeError) as e:
            self.status.set_description(f"Bad response: {e}")
            self.stack.set_visible_child_name("error")
            return
        items = [ThreadItem(t, self.board, i) for i, t in enumerate(threads)]
        self.store.splice(0, len(self.store), items)
        self.stack.set_visible_child_name("grid")
