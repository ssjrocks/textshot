#!/usr/bin/env python3
"""Textshot: pick an area with GNOME's screenshot UI, OCR it, and show the
text in a window where any part (or all) of it can be selected and copied.
The captured image is kept in the normal Screenshots folder.

Usage:
    textshot            capture an area of the screen
    textshot IMAGE      read text from an existing image file
"""

import os
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

APP_ID = "io.github.ssjrocks.Textshot"

# Load the OCR engine in the background while the user is still selecting.
_engine = None
_engine_ready = threading.Event()


def _load_engine():
    global _engine
    import logging
    from rapidocr import RapidOCR

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    _engine = RapidOCR()
    _engine_ready.set()


def ocr_to_text(path):
    """OCR an image and rebuild its lines, keeping column alignment."""
    _engine_ready.wait()
    # Screenshots are never rotated, so skip the direction classifier (it
    # sometimes flips a line upside down and garbles it).
    res = _engine(path, use_cls=False, return_word_box=True)
    if res.boxes is None or not len(res.boxes):
        return ""
    # Prefer word boxes: a detected line can span several table columns, and
    # word positions let us restore the spacing between them.
    words = getattr(res, "word_results", None) or ()
    pairs = [(b, w) for line in words for w, _, b in line if w.strip()]
    if not pairs:
        pairs = list(zip(res.boxes, res.txts))
    items = []
    for box, txt in pairs:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append((min(xs), max(xs), min(ys), max(ys), txt))

    heights = [b - t for _, _, t, b, _ in items]
    line_h = statistics.median(heights)
    widths = [(x1 - x0) / len(t) for x0, x1, _, _, t in items if len(t) >= 3]
    char_w = statistics.median(widths) if widths else line_h / 2
    left = min(i[0] for i in items)

    # Group boxes into lines by vertical centre.
    items.sort(key=lambda i: (i[2] + i[3]) / 2)
    lines = []
    for it in items:
        cy = (it[2] + it[3]) / 2
        if lines and abs(cy - lines[-1]["cy"]) < line_h * 0.5:
            lines[-1]["items"].append(it)
        else:
            lines.append({"cy": cy, "items": [it]})

    out = []
    prev_cy = None
    for ln in lines:
        if prev_cy is not None and ln["cy"] - prev_cy > line_h * 2.2:
            out.append("")  # keep visible blank lines
        prev_cy = ln["cy"]
        s = ""
        prev_x1 = None
        for x0, x1, _, _, txt in sorted(ln["items"], key=lambda i: i[0]):
            col = round((x0 - left) / char_w)
            if prev_x1 is None:
                s = " " * col
            elif x0 - prev_x1 < char_w * 1.6:
                s += " "
            else:
                s = s.ljust(max(col, len(s) + 1))
            s += txt
            prev_x1 = x1
        out.append(s.rstrip())
    return "\n".join(out)


def screenshots_dir():
    pics = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    return os.path.join(pics or os.path.expanduser("~/Pictures"), "Screenshots")


def keep_screenshot(path):
    """Make sure the capture ends up in the Screenshots folder, named the way
    GNOME names its own screenshots. Returns the final path."""
    dest_dir = screenshots_dir()
    if os.path.dirname(os.path.abspath(path)) == dest_dir:
        return path  # GNOME already saved it there
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    dest = os.path.join(dest_dir, f"Screenshot From {stamp}.png")
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f"Screenshot From {stamp} ({n}).png")
        n += 1
    shutil.move(path, dest)
    return dest


def copy_text(text):
    # wl-copy keeps serving the clipboard after this window closes.
    subprocess.run(["wl-copy", "--", text], check=False)


class Textshot(Adw.Application):
    def __init__(self, image=None):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.image = image
        self.win = None
        self.bus = None
        self.current_image = None
        self.on_capture = self.on_response

    def do_activate(self):
        if self.image:
            self.show_window()
            self.read_image(self.image, keep=False)
            return
        self.hold()  # stay alive while the screenshot UI has focus
        self.request_screenshot()

    # --- screenshot via xdg-desktop-portal (shows GNOME's own picker) ---
    def request_screenshot(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        token = f"textshot{random.randint(0, 1 << 30)}"
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        self._sub = self.bus.signal_subscribe(
            "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
            "Response", handle, None, Gio.DBusSignalFlags.NO_MATCH_RULE,
            lambda *a: self.on_capture(*a))
        opts = {"interactive": GLib.Variant("b", True),
                "handle_token": GLib.Variant("s", token)}
        self.bus.call(
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Screenshot", "Screenshot",
            GLib.Variant("(sa{sv})", ("", opts)), None,
            Gio.DBusCallFlags.NONE, -1, None, None)

    def on_response(self, bus, sender, path, iface, signal, params):
        bus.signal_unsubscribe(self._sub)
        code, results = params.unpack()
        uri = results.get("uri")
        if code != 0 or not uri:
            if self.win is None:
                self.release()  # cancelled before any window: just exit
            return
        self.show_window()
        self.read_image(unquote(urlparse(uri).path), keep=True)

    def read_image(self, img, keep):
        self.set_busy(True)

        def work():
            saved = img
            if keep:
                try:
                    saved = keep_screenshot(img)
                except OSError as e:
                    GLib.idle_add(self.toast, f"Could not save image: {e}")
            try:
                text = ocr_to_text(saved)
            except Exception as e:  # show the error rather than vanish
                text = f"OCR failed: {e}"
            GLib.idle_add(self.show_text, text, saved)

        threading.Thread(target=work, daemon=True).start()

    # --- result window ---
    def show_window(self):
        if self.win is not None:
            self.win.present()
            return
        self.win = Adw.ApplicationWindow(application=self, title="Textshot",
                                         default_width=760, default_height=480)
        if not self.image:
            self.release()

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Textshot")
        header.set_title_widget(self.title)
        again = Gtk.Button(icon_name="camera-photo-symbolic",
                           tooltip_text="New capture (Ctrl+N)")
        again.connect("clicked", lambda *_: self.new_capture())
        header.pack_start(again)
        open_img = Gtk.Button(icon_name="image-x-generic-symbolic",
                              tooltip_text="Open the screenshot")
        open_img.connect("clicked", lambda *_: self.open_image())
        header.pack_start(open_img)
        copy_all = Gtk.Button(label="Copy all")
        copy_all.add_css_class("suggested-action")
        copy_all.connect("clicked", lambda *_: self.copy_all())
        header.pack_end(copy_all)
        self.copy_sel = Gtk.Button(label="Copy selection")
        self.copy_sel.connect("clicked", lambda *_: self.copy_selection())
        header.pack_end(self.copy_sel)
        tv.add_top_bar(header)

        self.stack = Gtk.Stack()
        spinner = Adw.Spinner(width_request=48, height_request=48)
        self.stack.add_named(spinner, "busy")
        self.view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE,
                                 left_margin=12, right_margin=12,
                                 top_margin=12, bottom_margin=12)
        self.view.connect("copy-clipboard", self.on_ctrl_c)
        self.view.get_buffer().connect(
            "notify::has-selection",
            lambda b, _: self.copy_sel.set_sensitive(b.get_has_selection()))
        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.stack.add_named(scroller, "text")
        tv.set_content(self.stack)
        self.toasts.set_child(tv)
        self.win.set_content(self.toasts)

        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for accel, fn in (("Escape", self.quit),
                          ("<Control>n", self.new_capture),
                          ("<Control><Shift>c", self.copy_all)):
            keys.add_shortcut(Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string(accel),
                action=Gtk.CallbackAction.new(lambda *_, f=fn: f() or True)))
        self.win.add_controller(keys)
        self.win.present()

    def set_busy(self, busy):
        self.stack.set_visible_child_name("busy" if busy else "text")

    def show_text(self, text, img):
        self.current_image = img
        self.title.set_subtitle(os.path.basename(img))
        buf = self.view.get_buffer()
        buf.set_text(text or "(no text found)")
        buf.place_cursor(buf.get_start_iter())
        self.copy_sel.set_sensitive(False)
        self.set_busy(False)
        self.view.grab_focus()

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=2))

    def open_image(self):
        if self.current_image:
            Gtk.FileLauncher.new(Gio.File.new_for_path(self.current_image)) \
                .launch(self.win, None, None)

    def copy_all(self):
        buf = self.view.get_buffer()
        copy_text(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
        self.toast("Copied all text")

    def copy_selection(self):
        buf = self.view.get_buffer()
        b = buf.get_selection_bounds()
        if b:
            copy_text(buf.get_text(b[0], b[1], False))
            self.toast("Copied selection")

    def on_ctrl_c(self, view):
        view.stop_emission_by_name("copy-clipboard")
        self.copy_selection()

    def new_capture(self):
        self.win.set_visible(False)
        self.hold()

        # Re-show the window whether the user captures or cancels.
        def once(*a):
            self.on_capture = self.on_response
            self.release()
            self.win.set_visible(True)
            self.on_response(*a)

        self.on_capture = once
        GLib.timeout_add(250, lambda: self.request_screenshot() and False)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    image = os.path.abspath(args[0]) if args else None
    if image and not os.path.isfile(image):
        print(f"textshot: no such file: {args[0]}", file=sys.stderr)
        return 1
    threading.Thread(target=_load_engine, daemon=True).start()
    return Textshot(image).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
