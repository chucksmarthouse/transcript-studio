#!/usr/bin/env bash
# Install a desktop launcher for Transcript Studio and pin it to the
# Ubuntu Dock / GNOME favorites. Re-run safely; --uninstall removes it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="transcript-studio"   # must match QGuiApplication.setDesktopFileName in __main__.py
DESKTOP="$HOME/.local/share/applications/$APP_ID.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"

favorites() { gsettings get org.gnome.shell favorite-apps 2>/dev/null || echo "[]"; }

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$DESKTOP" "$ICON"
    if command -v gsettings >/dev/null; then
        gsettings set org.gnome.shell favorite-apps \
            "$(favorites | python3 -c "import ast,sys; f=ast.literal_eval(sys.stdin.read()); print([x for x in f if x != '$APP_ID.desktop'])")"
    fi
    echo "Removed launcher and dock entry."
    exit 0
fi

install -Dm644 "$ROOT/assets/$APP_ID.svg" "$ICON"
mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Transcript Studio
GenericName=Transcription & Review
Comment=Transcribe, review and export color-coded transcripts
Exec="$ROOT/.venv/bin/python" -m transcript_studio %f
Path=$ROOT
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Audio;
StartupWMClass=$APP_ID
StartupNotify=true
EOF
chmod 644 "$DESKTOP"
update-desktop-database -q "$(dirname "$DESKTOP")" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

if command -v gsettings >/dev/null; then
    gsettings set org.gnome.shell favorite-apps \
        "$(favorites | python3 -c "import ast,sys; f=ast.literal_eval(sys.stdin.read()); print(f if '$APP_ID.desktop' in f else f + ['$APP_ID.desktop'])")"
    echo "Pinned to the dock."
fi
echo "Installed $DESKTOP"
