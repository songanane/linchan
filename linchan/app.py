import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, Gio, Gtk

from . import APP_ID
from .net import Net, prune_cache
from .state import State
from .style import CSS
from .window import Window


class Linchan(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.net, self.state = None, None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.net, self.state = Net(), State()
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.quit())
        self.add_action(quit_)
        for action, keys in (("app.quit", ["<Ctrl>q"]), ("win.open-url", ["<Ctrl>l", "<Ctrl>o"]), ("win.refresh", ["<Ctrl>r", "F5"]),
                             ("win.back", ["<Alt>Left", "BackSpace"]), ("win.toggle-sidebar", ["F9"])):
            self.set_accels_for_action(action, keys)
        threading.Thread(target=prune_cache, daemon=True).start()

    def do_activate(self):
        win = self.props.active_window or Window(self, self.net, self.state)
        win.present()


def main(argv=None):
    return Linchan().run(argv or sys.argv)
