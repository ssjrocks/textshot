#!/usr/bin/env python3
"""Regenerate the README images in docs/screenshots/.

Draws a sample terminal image, opens Textshot on it, and renders the result
window (plain and with a selection) to PNG. Run with the installed venv:
    ~/.local/share/textshot/venv/bin/python tools/make_screenshots.py
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
sys.path.insert(0, ROOT)

import textshot  # noqa: E402
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

PROMPT = "user@ubuntu"
SAMPLE = [
    ("$", "df -h -x tmpfs"),
    ("", "Filesystem      Size  Used Avail Use% Mounted on"),
    ("", "/dev/nvme0n1p2  915G  312G  557G  36% /"),
    ("", "/dev/nvme0n1p1  1.1G  6.2M  1.1G   1% /boot/efi"),
    ("", "/dev/sda1       1.8T  1.2T  562G  69% /mnt/backup"),
    ("$", "ip -br addr"),
    ("", "lo               UNKNOWN        127.0.0.1/8 ::1/128"),
    ("", "enp5s0           UP             192.168.1.42/24 fe80::3a2c:4aff:fe1b:9c10/64"),
    ("$", "systemctl --failed --no-legend | wc -l"),
    ("", "0"),
    ("$", ""),
]


def draw_terminal(path):
    font = ImageFont.truetype("/usr/share/fonts/truetype/ubuntu/UbuntuMono[wght].ttf", 26)
    cw = font.getlength("M")
    lh = 34
    w = int(cw * 82) + 40
    h = lh * len(SAMPLE) + 40
    im = Image.new("RGB", (w, h), "#300a24")
    d = ImageDraw.Draw(im)
    y = 20
    for kind, text in SAMPLE:
        x = 20
        if kind == "$":
            d.text((x, y), PROMPT, font=font, fill="#8ae234")
            x += cw * len(PROMPT)
            d.text((x, y), ":", font=font, fill="#ffffff")
            d.text((x + cw, y), "~", font=font, fill="#729fcf")
            d.text((x + cw * 2, y), "$ " + text, font=font, fill="#ffffff")
        else:
            d.text((x, y), text, font=font, fill="#ffffff")
        y += lh
    im.save(path)


def render(widget, path, scale=2):
    # Draw as the focused window even if another window has focus.
    widget.get_root().unset_state_flags(Gtk.StateFlags.BACKDROP)
    w, h = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    snap.scale(scale, scale)
    Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h)
    node = snap.to_node()
    rect = Graphene.Rect().init(0, 0, w * scale, h * scale)
    tex = widget.get_native().get_renderer().render_texture(node, rect)
    tex.save_to_png(path)
    frame(path, scale)


def frame(path, scale):
    """Round the corners and add a soft shadow, like a GNOME window."""
    win = Image.open(path).convert("RGBA")
    r = 12 * scale
    mask = Image.new("L", win.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, *win.size], r, fill=255)
    pad = 32 * scale
    canvas = Image.new("RGBA", (win.width + pad * 2, win.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [pad, pad + 4 * scale, pad + win.width, pad + win.height + 4 * scale], r,
        fill=(0, 0, 0, 90))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(10 * scale)))
    canvas.paste(win, (pad, pad), mask)
    canvas.save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    sample = os.path.join(OUT, "sample-terminal.png")
    draw_terminal(sample)
    textshot._load_engine()

    app = textshot.Textshot(sample)
    real_show = app.show_text

    def shoot_plain():
        render(app.win, os.path.join(OUT, "result.png"))
        buf = app.view.get_buffer()
        start = buf.get_iter_at_line(1)[1]
        end = buf.get_iter_at_line(5)[1]
        buf.select_range(start, end)
        app.toast("Copied selection")  # skip touching the real clipboard
        GLib.timeout_add(600, shoot_selection)
        return False

    def shoot_selection():
        render(app.win, os.path.join(OUT, "selection.png"))
        app.quit()
        return False

    def show_text(text, img):
        real_show(text, img)
        app.title.set_subtitle("Screenshot From 2026-10-11 14-02-17.png")
        app.win.set_default_size(980, 440)
        GLib.timeout_add(800, shoot_plain)

    app.show_text = show_text
    app.run([sys.argv[0]])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
