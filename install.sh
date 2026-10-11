#!/bin/sh
# Install Textshot for the current user.
#   ./install.sh              install (or update)
#   ./install.sh --desktop    also put a launcher on the Desktop
#   ./install.sh --uninstall  remove it again
set -e

SRC=$(cd "$(dirname "$0")" && pwd)
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/textshot"
BIN="$HOME/.local/bin/textshot"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="io.github.ssjrocks.Textshot.desktop"
USER_DESKTOP=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")

if [ "$1" = "--uninstall" ]; then
    rm -rf "$APP_DIR" "$BIN" "$APPS/$DESKTOP_FILE" "$USER_DESKTOP/$DESKTOP_FILE"
    update-desktop-database "$APPS" 2>/dev/null || true
    echo "Textshot removed."
    exit 0
fi

# System packages Textshot needs (GTK 4, libadwaita, Wayland clipboard).
missing=""
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" \
    2>/dev/null || missing="$missing python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
command -v wl-copy >/dev/null || missing="$missing wl-clipboard"
python3 -c "import venv, ensurepip" 2>/dev/null || missing="$missing python3-venv"
if [ -n "$missing" ]; then
    echo "Missing system packages. Install them with:"
    echo "  sudo apt install$missing"
    exit 1
fi

mkdir -p "$APP_DIR" "$(dirname "$BIN")" "$APPS"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    echo "Creating Python environment..."
    python3 -m venv --system-site-packages "$APP_DIR/venv"
fi
echo "Installing OCR engine (RapidOCR)..."
"$APP_DIR/venv/bin/pip" install -q --upgrade -r "$SRC/requirements.txt"

install -m 644 "$SRC/textshot.py" "$APP_DIR/textshot.py"
install -m 644 "$SRC/data/textshot.svg" "$APP_DIR/textshot.svg"

cat > "$BIN" <<EOS
#!/bin/sh
exec "$APP_DIR/venv/bin/python" "$APP_DIR/textshot.py" "\$@"
EOS
chmod +x "$BIN"

sed -e "s|@BIN@|$BIN|" -e "s|@ICON@|$APP_DIR/textshot.svg|" \
    "$SRC/data/textshot.desktop.in" > "$APPS/$DESKTOP_FILE"
update-desktop-database "$APPS" 2>/dev/null || true

if [ "$1" = "--desktop" ]; then
    install -m 755 "$APPS/$DESKTOP_FILE" "$USER_DESKTOP/$DESKTOP_FILE"
    gio set "$USER_DESKTOP/$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
fi

echo "Textshot installed. Find it in the app grid, or run: textshot"
