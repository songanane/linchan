"""Fullscreen-capable media viewer (images, animated GIFs, webm/mp4)."""

import shutil
import subprocess
from urllib.parse import quote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from .net import get_texture  # noqa: F401  (re-exported for convenience)

# Reverse image search engines; {} receives the URL-encoded image URL.
SEARCH_ENGINES = [
    ("Google Lens", "https://lens.google.com/uploadbyurl?url={}"),
    ("Yandex", "https://yandex.com/images/search?rpt=imageview&url={}"),
    ("IQDB", "https://iqdb.org/?url={}"),
    ("SauceNAO", "https://saucenao.com/search.php?url={}"),
    ("TinEye", "https://tineye.com/search?url={}"),
]


class GifPlayer:
    """Drives a Gtk.Picture from a GdkPixbufAnimation."""

    def __init__(self, path, picture):
        self.picture, self.src, self.alive = picture, 0, True
        self.anim = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
        self.iter = self.anim.get_iter(None)
        self._show()

    def _show(self):
        if not self.alive:
            return
        self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(self.iter.get_pixbuf()))
        delay = self.iter.get_delay_time()
        if delay >= 0 and not self.anim.is_static_image():
            self.src = GLib.timeout_add(max(delay, 20), self._advance)

    def _advance(self):
        self.src = 0
        self.iter.advance(None)
        self._show()
        return False

    def stop(self):
        self.alive = False
        if self.src:
            GLib.source_remove(self.src)
            self.src = 0


class Viewer(Adw.Window):
    def __init__(self, parent, net, posts, index, on_goto=None):
        super().__init__(transient_for=parent, modal=True, title="Media")
        self.add_css_class("linchan-viewer")
        self.set_default_size(1100, 800)
        self.net, self.posts, self.index, self.token = net, posts, index, 0
        self.gif = None
        self.zoomed = False
        self.on_goto = on_goto

        # --- media area
        self.pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.scroll = Gtk.ScrolledWindow(child=self.pic, hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.NEVER)
        click = Gtk.GestureClick()
        click.connect("released", self._toggle_zoom)
        self.scroll.add_controller(click)
        self.video = Gtk.Video(autoplay=True, loop=True)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["title-3"])
        self.progress = Gtk.ProgressBar(width_request=260, halign=Gtk.Align.CENTER)
        spin_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        spin_box.append(self.status)
        spin_box.append(self.progress)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        self.stack.add_named(self.scroll, "image")
        self.stack.add_named(self.video, "video")
        self.stack.add_named(spin_box, "status")

        # --- overlay controls
        self.title_lbl = Gtk.Label(hexpand=True, xalign=0, ellipsize=3)
        close = self._btn("window-close-symbolic", "Close (Esc)", lambda *_: self.close())
        save = self._btn("document-save-symbolic", "Save as… (S)", lambda *_: self.save())
        ext = self._btn("external-link-symbolic", "Open externally (O)", lambda *_: self.open_external())
        full = self._btn("view-fullscreen-symbolic", "Fullscreen (F)", lambda *_: self.toggle_full())
        search = self._search_button()
        goto = self._btn("go-jump-symbolic", "Go to post (G)", lambda *_: self.goto_post())
        goto.set_sensitive(on_goto is not None)
        bar = Gtk.Box(spacing=6, valign=Gtk.Align.START, css_classes=["osd", "toolbar"], margin_top=8, margin_start=8, margin_end=8)
        bar.set_halign(Gtk.Align.FILL)
        for w in (close, self.title_lbl, search, save, ext, full, goto):
            bar.append(w)
        prev = self._btn("go-previous-symbolic", "Previous (←)", lambda *_: self.step(-1), osd=True)
        nxt = self._btn("go-next-symbolic", "Next (→)", lambda *_: self.step(1), osd=True)
        prev.set_halign(Gtk.Align.START)
        nxt.set_halign(Gtk.Align.END)
        for b in (prev, nxt):
            b.set_valign(Gtk.Align.CENTER)
            b.set_margin_start(12)
            b.set_margin_end(12)

        overlay = Gtk.Overlay(child=self.stack)
        for w in (bar, prev, nxt):
            overlay.add_overlay(w)
        self.set_content(overlay)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.show(index)

    def _search_button(self):
        """Menu button listing reverse-image-search engines."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        pop = Gtk.Popover(child=box)
        for name, template in SEARCH_ENGINES:
            b = Gtk.Button(label=name, css_classes=["flat"], halign=Gtk.Align.FILL)
            b.get_child().set_halign(Gtk.Align.START)
            b.connect("clicked", lambda _b, t=template: (pop.popdown(), self.reverse_search(t)))
            box.append(b)
        return Gtk.MenuButton(
            icon_name="edit-find-symbolic", tooltip_text="Search for this image", popover=pop, css_classes=["flat", "circular"]
        )

    def reverse_search(self, template):
        p = self.posts[self.index]
        # Videos have no still image online, so search by their thumbnail.
        image_url = p.thumb_url if p.is_video else p.media_url
        Gtk.UriLauncher(uri=template.format(quote(image_url, safe=""))).launch(self, None, None)

    def _btn(self, icon, tip, cb, osd=False):
        b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["osd", "circular"] if osd else ["flat", "circular"])
        b.connect("clicked", cb)
        return b

    # ---- navigation ------------------------------------------------------------
    def _key(self, _c, keyval, _code, _state):
        k = Gdk.keyval_name(keyval)
        if k == "Escape":
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.close()
        elif k in ("Left", "h", "a"):
            self.step(-1)
        elif k in ("Right", "l", "d"):
            self.step(1)
        elif k == "f" or k == "F11":
            self.toggle_full()
        elif k == "g":
            self.goto_post()
        elif k == "s":
            self.save()
        elif k == "o":
            self.open_external()
        elif k == "space" and self.stack.get_visible_child_name() == "video":
            s = self.video.get_media_stream()
            if s:
                s.set_playing(not s.get_playing())
        else:
            return False
        return True

    def toggle_full(self):
        self.unfullscreen() if self.is_fullscreen() else self.fullscreen()

    def step(self, delta):
        i = self.index + delta
        if 0 <= i < len(self.posts):
            self.show(i)

    # ---- loading -----------------------------------------------------------------
    def _stop_media(self):
        if self.gif:
            self.gif.stop()
            self.gif = None
        self.video.set_file(None)

    def show(self, i):
        self.index, self.token = i, self.token + 1
        tok, p = self.token, self.posts[i]
        self._stop_media()
        d = p.d
        self.title_lbl.set_label(f"{p.filename}  ·  {d.get('w', '?')}×{d.get('h', '?')}  ·  {len(self.posts) and i + 1}/{len(self.posts)}")
        self.status.set_label("Loading…")
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.stack.set_visible_child_name("status")
        self.net.download(
            p.media_url,
            p.media_path,
            lambda path, err: self._loaded(tok, p, path, err),
            progress=lambda f: self.progress.set_fraction(f) if tok == self.token else None,
        )
        for nb in (i + 1, i - 1):  # prefetch neighbours (images only)
            if 0 <= nb < len(self.posts) and not self.posts[nb].is_video:
                self.net.download(self.posts[nb].media_url, self.posts[nb].media_path, lambda *_: None)

    def _fail(self, msg):
        self.progress.set_visible(False)
        self.status.set_label(msg)
        self.stack.set_visible_child_name("status")

    def _loaded(self, tok, p, path, err):
        if tok != self.token:
            return
        if err:
            return self._fail(f"Could not load file\n{err}")
        self.zoomed = False
        self._apply_zoom()
        if p.is_video:
            self.stack.set_visible_child_name("video")
            self.video.set_file(Gio.File.new_for_path(str(path)))
            stream = self.video.get_media_stream()
            if stream:
                stream.connect(
                    "notify::error",
                    lambda s, _p: (
                        self._fail(f"Playback failed: {s.get_error().message}\nPress O to open in mpv.")
                        if s.get_error() and tok == self.token
                        else None
                    ),
                )
            return
        try:
            if p.ext == ".gif":
                self.gif = GifPlayer(path, self.pic)
            else:
                self.pic.set_paintable(Gdk.Texture.new_from_filename(str(path)))
        except GLib.Error as e:
            return self._fail(f"Could not decode image\n{e.message}")
        self.stack.set_visible_child_name("image")

    # ---- zoom --------------------------------------------------------------------
    def _toggle_zoom(self, *_):
        if self.stack.get_visible_child_name() != "image":
            return
        self.zoomed = not self.zoomed
        self._apply_zoom()

    def _apply_zoom(self):
        pol = Gtk.PolicyType.AUTOMATIC if self.zoomed else Gtk.PolicyType.NEVER
        self.scroll.set_policy(pol, pol)
        self.pic.set_can_shrink(not self.zoomed)

    # ---- actions -----------------------------------------------------------------
    def save(self):
        p = self.posts[self.index]
        dlg = Gtk.FileDialog(initial_name=p.filename)

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            Gio.File.new_for_path(str(p.media_path)).copy(f, Gio.FileCopyFlags.OVERWRITE, None, None, None)

        if p.media_path.exists():
            dlg.save(self, None, done)

    def goto_post(self):
        """Close the viewer and scroll the thread to the post holding this media."""
        if self.on_goto:
            no = self.posts[self.index].no
            self.close()
            GLib.idle_add(lambda: self.on_goto(no) and False)

    def open_external(self):
        p = self.posts[self.index]
        if p.is_video and shutil.which("mpv"):
            subprocess.Popen(
                ["mpv", "--loop", "--force-window=yes", p.media_url],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif p.media_path.exists():
            Gtk.FileLauncher(file=Gio.File.new_for_path(str(p.media_path))).launch(self, None, None)

    def do_close_request(self):
        self._stop_media()
        self.token += 1
        return False
