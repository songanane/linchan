"""Replying to threads.

Linchan composes the post natively (name, options, comment, attachment) but does
not talk to the posting endpoint itself. 4chan's posting path sits behind
Cloudflare and a multi-stage captcha that only a real browser can complete, so
the actual submission goes through the site's own Quick Reply, running in an
embedded WebKit view. Only the captcha part of that view is shown; the person
solves it exactly as they would on the website. Linchan never solves, forges or
bypasses any verification.
"""

import base64
import json
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, WebKit

from .net import WEB

MAX_COMMENT = 2000  # 4chan's comment limit
MAX_UPLOAD = 4 * 1024 * 1024
POLL_MS = 700
DRY_RUN = bool(os.environ.get("LINCHAN_DRY_RUN"))  # fill everything, but never send (for testing)

_session = None


def network_session():
    """One persistent WebKit session, so Cloudflare clearance and a 4chan Pass survive restarts."""
    global _session
    if _session is None:
        data = os.path.join(GLib.get_user_data_dir(), "linchan", "webkit")
        cache = os.path.join(GLib.get_user_cache_dir(), "linchan", "webkit")
        os.makedirs(data, exist_ok=True)
        os.makedirs(cache, exist_ok=True)
        _session = WebKit.NetworkSession.new(data, cache)
        # The captcha's verification runs in a cross-site iframe and loads a script from
        # mcl.io / spur.us. WebKit's tracking prevention treats that as a tracker and blocks
        # it, which the site reports as "Please unblock spur.us and mcl.io". This view only
        # ever shows 4chan, so behave like an ordinary browser here.
        _session.set_itp_enabled(False)
        _session.get_cookie_manager().set_accept_policy(WebKit.CookieAcceptPolicy.ALWAYS)
    return _session


def clear_browsing_data(done):
    """Delete everything the reply view stores: cookies, cache, local storage, Cloudflare clearance.

    `done(error)` is called on the main loop, with None on success or an error message."""
    manager = network_session().get_website_data_manager()

    def finished(mgr, result):
        try:
            mgr.clear_finish(result)
        except GLib.Error as e:
            done(e.message)
        else:
            done(None)

    manager.clear(WebKit.WebsiteDataTypes.ALL, 0, None, finished)  # timespan 0 = everything, not just recent data


# ---- scripts injected into the thread page ---------------------------------------

# Make the page transparent and leave only the captcha visible: hide every sibling on
# the path from the captcha container up to <body> and flatten the ancestors.
JS_ISOLATE = r"""
(() => {
  const cap = document.getElementById('qrCaptchaContainer');
  if (!cap) return 'no-captcha';
  document.documentElement.style.cssText += ';background:transparent!important;overflow:hidden!important';
  document.body.style.cssText += ';background:transparent!important;margin:0!important;overflow:hidden!important';
  for (let el = cap; el && el !== document.body; el = el.parentElement) {
    for (const sib of el.parentElement.children) {
      if (sib !== el && sib.tagName !== 'SCRIPT') sib.style.setProperty('display', 'none', 'important');
    }
    if (el !== cap) {
      el.style.cssText += ';position:static!important;margin:0!important;padding:0!important;border:0!important;' +
        'box-shadow:none!important;background:transparent!important;width:auto!important;min-width:0!important;' +
        'height:auto!important;transform:none!important;display:block!important';
    }
  }
  cap.style.setProperty('margin', '0 auto', 'important');
  return 'ok';
})()
"""

JS_OPEN = "(() => { try { QR.show(Main.tid); return 'ok'; } catch (e) { return 'error: ' + e; } })()"

JS_STATE = r"""
(() => {
  const o = {qr: typeof QR !== 'undefined' && !!document.getElementById('quickReply')};
  const r = document.getElementById('t-resp');
  o.verified = !!(r && r.value);
  const e = document.getElementById('qrError');
  o.error = (e && getComputedStyle(e).display !== 'none') ? e.textContent.trim() : '';
  o.last = typeof QR !== 'undefined' ? (+QR.lastReplyId || 0) : 0;
  o.busy = typeof QR !== 'undefined' && !!QR.xhr;
  o.cooldown = typeof QR !== 'undefined' ? (+QR.cooldown || 0) : 0;
  o.dry = window.__linchanDry || null;
  return JSON.stringify(o);
})()
"""

# %s receives a JSON object: {name, email, com, dry, file: {name, type, b64} | null}
JS_SUBMIT = r"""
((a) => {
  const f = document.forms.qrPost;
  if (!f) return 'no-form';
  f.elements['name'].value = a.name;
  f.elements['email'].value = a.email;
  f.elements['com'].value = a.com;
  if (a.file) {
    const bytes = Uint8Array.from(atob(a.file.b64), c => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], a.file.name, {type: a.file.type}));
    const inp = document.getElementById('qrFile');
    inp.files = dt.files;
    inp.dispatchEvent(new Event('change', {bubbles: true}));
  }
  if (a.dry) {   // testing only: record what would be sent, and do not send it
    XMLHttpRequest.prototype.send = function (body) {
      const keys = []; for (const k of body.keys()) keys.push(k);
      window.__linchanDry = {keys: keys, com_len: (body.get('com') || '').length, has_file: !!(body.get('upfile') && body.get('upfile').size)};
    };
  }
  QR.submit();
  return 'submitted';
})(%s)
"""


class ReplyDialog(Adw.Dialog):
    def __init__(self, win, board, tid, prefill="", on_posted=None):
        super().__init__(title="Reply", content_width=600, content_height=720)
        self.win, self.board, self.tid, self.on_posted = win, board, int(tid), on_posted
        self.file = None  # Gio.File
        self.state = "loading"  # loading | ready | posting | done | failed
        self.last0 = None  # QR.lastReplyId before we posted
        self.poll_src = 0
        self.url = f"{WEB}/{board}/thread/{tid}"

        # ---- fields
        self.name_row = Adw.EntryRow(title="Name (optional)", text=win.state.data.get("poster_name", ""))
        self.opts_row = Adw.EntryRow(title="Options (e.g. sage)")
        fields = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        fields.append(self.name_row)
        fields.append(self.opts_row)

        self.comment = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=10, right_margin=10, accepts_tab=False
        )
        self.comment.get_buffer().set_text(prefill)
        self.comment.get_buffer().connect("changed", self._on_changed)
        scroll = Gtk.ScrolledWindow(child=self.comment, min_content_height=150, vexpand=True, css_classes=["card"])
        self.counter = Gtk.Label(xalign=1, css_classes=["caption", "dim-label"])

        self.file_row = Adw.ActionRow(title="Attachment", subtitle="None")
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._choose_file)
        self.clear_btn = Gtk.Button(
            icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], visible=False, tooltip_text="Remove attachment"
        )
        self.clear_btn.connect("clicked", self._clear_file)
        self.file_row.add_suffix(self.clear_btn)
        self.file_row.add_suffix(choose)
        file_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        file_list.append(self.file_row)

        # ---- verification: the site's own captcha, in an embedded browser view
        ucm = WebKit.UserContentManager()
        self.view = WebKit.WebView(network_session=network_session(), user_content_manager=ucm, width_request=316, height_request=170)
        self.view.set_background_color(Gdk.RGBA())
        self.view.connect("load-changed", self._on_load)
        self.view.connect("create", self._on_create)
        if os.environ.get("LINCHAN_DEBUG"):
            self.view.connect("resource-load-started", self._log_resource)
        frame = Gtk.Frame(child=self.view, halign=Gtk.Align.CENTER)

        self.status = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, css_classes=["caption"])
        self.open_web = Gtk.Button(label="Open thread in browser", halign=Gtk.Align.START, visible=False, css_classes=["flat"])
        self.open_web.connect("clicked", lambda *_: Gtk.UriLauncher(uri=self.url).launch(self.win, None, None))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=16, margin_start=16, margin_end=16)
        comment_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)  # counter sits close under the comment
        comment_box.append(scroll)
        comment_box.append(self.counter)
        for w in (
            fields,
            comment_box,
            file_list,
            Gtk.Label(label="Verification", xalign=0, css_classes=["heading"]),
            frame,
            self.status,
            self.open_web,
        ):
            box.append(w)

        self.post_btn = Gtk.Button(label="Post", css_classes=["suggested-action"], sensitive=False)
        self.post_btn.connect("clicked", self._post)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(self.post_btn)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        view.add_top_bar(header)
        self.set_child(view)
        self.set_default_widget(None)

        self.connect("closed", self._on_closed)
        self._set_status("Loading the posting form…")
        self.view.load_uri(self.url)
        self.load_timeout = GLib.timeout_add_seconds(25, self._load_timed_out)
        self.poll_src = GLib.timeout_add(POLL_MS, self._poll)
        self._on_changed()

    # ---- helpers -------------------------------------------------------------------
    def _text(self):
        b = self.comment.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def _set_status(self, text, error=False):
        self.status.set_label(text)
        (self.status.add_css_class if error else self.status.remove_css_class)("error")

    def _on_changed(self, *_):
        n = len(self._text())
        self.counter.set_label(f"{n} / {MAX_COMMENT}")
        (self.counter.add_css_class if n > MAX_COMMENT else self.counter.remove_css_class)("error")
        self._update_post_btn()

    def _update_post_btn(self):
        n = len(self._text().strip())
        content_ok = (n > 0 or self.file is not None) and n <= MAX_COMMENT
        self.post_btn.set_sensitive(self.state == "ready" and self._verified and content_ok)

    _verified = False

    def _eval(self, js, cb=None):
        def done(view, res):
            try:
                val = view.evaluate_javascript_finish(res)
                if cb:
                    cb(val.to_string() if val is not None else "")
            except GLib.Error as e:
                if cb:
                    cb("error: " + e.message)

        self.view.evaluate_javascript(js, -1, None, None, None, done)

    # ---- attachment ----------------------------------------------------------------------
    def _choose_file(self, *_):
        dlg = Gtk.FileDialog(title="Attach an image or video")
        flt = Gtk.FileFilter(name="Images and video")
        for mime in ("image/jpeg", "image/png", "image/gif", "video/webm", "video/mp4"):
            flt.add_mime_type(mime)
        dlg.set_default_filter(flt)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            info = f.query_info("standard::size,standard::display-name", Gio.FileQueryInfoFlags.NONE, None)
            size = info.get_size()
            if size > MAX_UPLOAD:
                self._set_status(f"That file is {size / 1048576:.1f} MB; the limit is {MAX_UPLOAD // 1048576} MB.", error=True)
                return
            self.file = f
            self.file_row.set_subtitle(f"{info.get_display_name()}  ·  {size / 1024:.0f} KB")
            self.clear_btn.set_visible(True)
            self._update_post_btn()

        dlg.open(self.win, None, done)

    def _clear_file(self, *_):
        self.file = None
        self.file_row.set_subtitle("None")
        self.clear_btn.set_visible(False)
        self._update_post_btn()

    # ---- embedded browser -----------------------------------------------------------------
    def _log_resource(self, _view, resource, _request):
        """LINCHAN_DEBUG=1: print each request the verification makes (no query strings, so no tokens)."""
        url = resource.get_uri().split("?")[0]
        if not any(h in url for h in ("4chan.org", "mcl.", "spur.us", "cloudflare.com", "4cdn.org")):
            return
        resource.connect("failed", lambda _r, err: print(f"[linchan] FAILED   {url}: {err.message}", flush=True))
        resource.connect(
            "finished",
            lambda r: print(
                f"[linchan] finished {url} status={r.get_response().get_status_code() if r.get_response() else '?'}", flush=True
            ),
        )

    def _on_create(self, view, action):
        """A page asked for a new window. Send real links to the system browser.

        Navigation inside the view must NOT be intercepted: the captcha works by
        loading sys.4chan.org (and Cloudflare / Turnstile pages) into frames in this
        view, starting from about:blank."""
        uri = action.get_request().get_uri() or ""
        if uri.startswith(("http://", "https://")):
            Gtk.UriLauncher(uri=uri).launch(self.win, None, None)
        return None

    def _on_load(self, view, event):
        if event == WebKit.LoadEvent.FINISHED and self.state == "loading":
            GLib.timeout_add(1500, self._open_quick_reply)

    def _open_quick_reply(self):
        if self.state != "loading":
            return False

        def opened(res):
            if res != "ok":
                return self._failed(f"The posting form could not be opened ({res}).")
            GLib.timeout_add(1200, lambda: (self._eval(JS_ISOLATE, isolated), False)[-1])

        def isolated(res):
            if res != "ok":
                return self._failed("The verification step did not appear.")
            self._eval(JS_STATE, got_state)

        def got_state(s):
            try:
                self.last0 = json.loads(s).get("last", 0)
            except ValueError:
                self.last0 = 0
            if self.load_timeout:
                GLib.source_remove(self.load_timeout)
                self.load_timeout = 0
            self.state = "ready"
            self._set_status("Press “Get Captcha” above and solve it, then write your reply.")
            self._update_post_btn()

        self._eval(JS_OPEN, opened)
        return False

    def _load_timed_out(self):
        self.load_timeout = 0
        if self.state == "loading":
            self._failed("Timed out loading the posting form. Cloudflare may be asking for a check, or the thread is closed.")
        return False

    def _failed(self, msg):
        self.state = "failed"
        self._set_status(msg, error=True)
        self.open_web.set_visible(True)
        self._update_post_btn()

    # ---- polling ---------------------------------------------------------------------------
    def _poll(self):
        if self.state in ("ready", "posting"):
            self._eval(JS_STATE, self._on_state)
        return True

    def _on_state(self, s):
        try:
            st = json.loads(s)
        except ValueError:
            return
        self._verified = bool(st.get("verified"))
        if self.state == "ready":
            if self._verified:
                self._set_status("Verified. Ready to post.")
            self._update_post_btn()
            return
        # state == posting
        if st.get("dry"):
            d = st["dry"]
            self._dry_report = d
            self._set_status(f"DRY RUN, nothing sent. Fields: {', '.join(d['keys'])}; comment {d['com_len']} chars; file: {d['has_file']}")
            self.state = "ready"
            self._update_post_btn()
        elif st.get("last") and st["last"] != self.last0:
            self.state = "done"
            pid = st["last"]
            if self.on_posted:
                self.on_posted(pid)
            self.close()
        elif st.get("error"):
            self.state = "ready"
            self._set_status(st["error"], error=True)
            self._verified = False
            self._enable_inputs(True)
            self._update_post_btn()
        elif st.get("cooldown"):
            self._set_status(f"Cooldown: posting automatically in {st['cooldown']} s…")

    def _enable_inputs(self, on):
        for w in (self.name_row, self.opts_row, self.comment):
            w.set_sensitive(on)

    # ---- posting ------------------------------------------------------------------------------
    def _post(self, *_):
        text = self._text().strip()
        name = self.name_row.get_text().strip()
        self.win.state["poster_name"] = name
        payload = {"name": name, "email": self.opts_row.get_text().strip(), "com": text, "dry": DRY_RUN, "file": None}
        if self.file is not None:
            try:
                data = self.file.load_bytes(None)[0].get_data()
            except GLib.Error as e:
                return self._set_status(f"Could not read the attachment: {e.message}", error=True)
            info = self.file.query_info("standard::content-type", Gio.FileQueryInfoFlags.NONE, None)
            payload["file"] = {
                "name": self.file.get_basename(),
                "type": info.get_content_type() or "application/octet-stream",
                "b64": base64.b64encode(data).decode(),
            }
        self.state = "posting"
        self._enable_inputs(False)
        self.post_btn.set_sensitive(False)
        self._set_status("Posting…")

        def submitted(res):
            if res != "submitted":
                self.state = "ready"
                self._enable_inputs(True)
                self._set_status(f"Could not submit ({res}).", error=True)
                self._update_post_btn()

        self._eval(JS_SUBMIT % json.dumps(payload), submitted)

    def _on_closed(self, *_):
        for src in (self.poll_src, self.load_timeout):
            if src:
                GLib.source_remove(src)
        self.poll_src = self.load_timeout = 0
        self.state = "done" if self.state == "done" else "closed"
        self.view.terminate_web_process()
