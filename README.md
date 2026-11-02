# Linchan

A native GTK 4 + libadwaita imageboard browser for Linux. Python and PyGObject, no web engine.
It reads the public, read-only 4chan JSON API and follows its rules (at most one request per
second, conditional `If-Modified-Since` requests).

Linchan is an unofficial client. It is not affiliated with or endorsed by 4chan.

![Catalog](screenshots/catalog.png)
![Thread](screenshots/thread.png)

## Features
- Board list with search, optional hiding of not-safe-for-work boards
- Catalog grid: filter, sort by bump order, last reply, replies, images, newest or oldest
- Virtualised thread view with greentext, spoilers, `>>` quote links that jump to the post, reply links and cross-thread links
- Hover a `>>` link to preview the quoted post; plain URLs in posts are clickable
- Live refresh every 15 s, "N new posts" toast, 404 and archived banners
- Media viewer: images, animated GIFs, webm/mp4, prefetching, click to zoom, fullscreen, save, open externally, jump to the post, reverse image search (Google Lens, Yandex, IQDB, SauceNAO, TinEye)
- Reply to threads (and quote a single post), with optional file attachments, using the site's own captcha
- Download every file in a thread to a folder you choose
- Watch list with unread counts, polled every 90 s
- Open any thread or board URL with Ctrl+L

## Keyboard
| Key | Action |
| --- | --- |
| Ctrl+L | Open a URL or board |
| Ctrl+R / F5 | Refresh |
| Alt+Left / Backspace | Back |
| F9 | Toggle the board sidebar |
| Ctrl+F | Filter the catalog |

In the media viewer: ←/→ navigate, F fullscreen, S save, O open externally, G go to post, Space pause, Esc close.

## Install

### Flatpak (build locally)
```sh
flatpak install flathub org.gnome.Platform//51 org.gnome.Sdk//51
flatpak run org.flatpak.Builder --user --install --force-clean build-flatpak io.github.songanane.Linchan.yml
flatpak run io.github.songanane.Linchan
```
`flatpak-builder` is available as `org.flatpak.Builder` on Flathub.

### Meson
Needs Python 3, PyGObject, GTK 4.12+, libadwaita 1.5+ and GStreamer good plugins (webm playback).
```sh
meson setup build --prefix=/usr/local
meson compile -C build
meson test -C build        # validates the desktop file and AppStream metadata
sudo meson install -C build
```

### Run from a checkout
```sh
./bin/linchan              # or: python3 -m linchan
```

## Notes
- **Replying.** The reply dialog composes natively but submits through the site's own Quick Reply in an embedded WebKitGTK view, because posting sits behind Cloudflare and a multi-stage captcha that only a real browser can complete. Only the captcha is shown; you solve it yourself. Linchan never solves, forges or bypasses verification. Replies only for now: no new threads, no 4chan Pass login screen. A Pass you log into elsewhere is not shared with this view.
- **Testing without posting.** Set `LINCHAN_DRY_RUN=1` and the dialog fills the form but never sends it, and reports which fields would have been sent.
- **Posting is unofficial.** 4chan's official API is read-only, so posting through a third-party app may break when the site changes, and the site may ban abusive use.
- **Video.** webm plays through GStreamer. mp4 (H.264) needs a codec your system provides; the Flatpak may fall back to "open externally".
- **Data.** Settings and the watch list live in `$XDG_CONFIG_HOME/linchan/state.json`. The media cache lives in `$XDG_CACHE_HOME/linchan` and is pruned after 7 days.
- **Sandbox.** The Flatpak has network, display, GPU and audio access only. Saving files goes through the file-chooser portal.

## Publishing to Flathub
1. Create the GitHub repository `songanane/linchan`, push this tree, and tag `v0.1.0`.
2. Commit `screenshots/catalog.png` and `screenshots/thread.png` (the metainfo links to them at that tag).
3. In the manifest, replace the `type: dir` source with the `type: git` source shown in its comment.
4. Check with `flatpak-builder-lint` (see above), then open a pull request against `flathub/flathub` following https://docs.flathub.org/docs/for-app-authors/submission.

## License
GPL-3.0-or-later. See `LICENSE`.
