#!/usr/bin/env bash
# Install the DeepSeek Harness Linux desktop shell into the user's profile:
# icons (hicolor theme), desktop entry, and a launcher symlink. The dsh
# checkout this project bundles is used as-is; when it is missing, it is
# cloned from GitHub and the bundled UI patches are applied on top.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_BIN="$HOME/.local/bin"
DEST_ICONS="$HOME/.local/share/icons/hicolor"
DEST_APPS="$HOME/.local/share/applications"
APP_ID="deepseek-harness-linux"

log() { printf '\033[1;34m[dsh-linux]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[dsh-linux]\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null || fail "python3 is required"
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('WebKit','6.0')" \
  || fail "python3-gi with Gtk 4 and WebKitGTK 6.0 is required (Debian/Ubuntu: gir1.2-gtk-4.0, gir1.2-webkit-6.0, python3-gi; Fedora: python3-gobject, gtk4, webkitgtk6.0)"
command -v node >/dev/null || fail "Node.js >= 24.9 is required (the app also auto-discovers nvm/volta/asdf/mise/fnm installs at startup)"
if [ -d "$ROOT/vendor/pnpm" ]; then
  log "Using the bundled pnpm (no system pnpm needed)."
else
  command -v pnpm >/dev/null || fail "pnpm is required"
fi
command -v git >/dev/null || fail "git is required"
log "Tool paths: node=$(command -v node) pnpm=$(command -v pnpm) git=$(command -v git)"
log "The app re-resolves these at startup, so version-managed installs (nvm/volta/…) keep working from the desktop."

# --- DeepSeek Harness checkout -----------------------------------------------
# git am records committer identity; supply one so machines without a
# configured user.name/user.email can still install.
GIT_IDENTITY=(-c user.name='DeepSeek Harness Linux' -c user.email='dsh-linux@localhost')
if [ ! -d "$ROOT/third_party/dsh" ]; then
  log "Cloning deepseek-harness from GitHub…"
  mkdir -p "$ROOT/third_party"
  git clone https://github.com/deepseek-ai/deepseek-harness.git "$ROOT/third_party/dsh"
  if compgen -G "$ROOT/patches/*.patch" > /dev/null; then
    log "Applying the desktop UI patches…"
    git -C "$ROOT/third_party/dsh" "${GIT_IDENTITY[@]}" am "$ROOT"/patches/*.patch
  fi
  UPSTREAM="$(git -C "$ROOT/third_party/dsh" rev-parse HEAD~"$(ls "$ROOT"/patches/*.patch 2>/dev/null | wc -l)" 2>/dev/null || git -C "$ROOT/third_party/dsh" rev-parse HEAD)"
  git -C "$ROOT/third_party/dsh" update-ref refs/desktop/patch-base "$UPSTREAM" 2>/dev/null || true
  git -C "$ROOT/third_party/dsh" update-ref refs/desktop/patch-tip HEAD
fi

# --- Icons -------------------------------------------------------------------
log "Installing hicolor icons…"
for size in 512 256 128 96 64 48 32 24 22 16; do
  mkdir -p "$DEST_ICONS/${size}x${size}/apps"
  cp "$ROOT/assets/icon-coloured-${size}.png" "$DEST_ICONS/${size}x${size}/apps/deepseek-harness.png"
  mkdir -p "$DEST_ICONS/${size}x${size}@2/apps"
  # 2x-scaled entries keep HiDPI panels from upscaling a small bitmap.
  double=$((size * 2))
  if [ -f "$ROOT/assets/icon-coloured-${double}.png" ]; then
    cp "$ROOT/assets/icon-coloured-${double}.png" "$DEST_ICONS/${size}x${size}@2/apps/deepseek-harness.png"
  fi
done
mkdir -p "$DEST_ICONS/scalable/apps"
# The real vector source (official DeepSeek glyph), not a PNG-in-SVG wrapper:
# desktop environments rasterize it at any size.
cp "$ROOT/assets/deepseek-icon.svg" "$DEST_ICONS/scalable/apps/deepseek-harness.svg"
python3 - "$ROOT/assets/deepseek-icon.svg" "$DEST_ICONS/scalable/apps/deepseek-harness-symbolic.svg" <<'PY'
import re, sys
svg = open(sys.argv[1], encoding='utf-8').read()
# Symbolic icon: the brand blue path becomes the symbolic foreground color.
svg = re.sub(r'fill="#4[dD]6[bB][fF][eE]"', 'fill="#ffffff"', svg)
open(sys.argv[2], 'w', encoding='utf-8').write(svg)
PY
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f "$DEST_ICONS" >/dev/null 2>&1 || true

# --- Launcher and desktop entry -----------------------------------------------
log "Installing the launcher and desktop entry…"
mkdir -p "$DEST_BIN" "$DEST_APPS"
ln -sf "$ROOT/bin/dsh-linux" "$DEST_BIN/dsh-linux"
cat > "$DEST_APPS/${APP_ID}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DeepSeek Harness
Comment=Agent harness desktop for Linux
Exec=$ROOT/bin/dsh-linux %u
Icon=deepseek-harness
Terminal=false
Categories=Development;Chat;AI;
MimeType=x-scheme-handler/dsh;
StartupWMClass=com.deepseek.dsh-linux
Keywords=deepseek;ai;agent;
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$DEST_APPS" >/dev/null 2>&1 || true

log "Installed. Launch with: $DEST_BIN/dsh-linux"
log "GNOME note: the status-area icon needs the 'AppIndicator and KStatusNotifierItem Support' extension;"
log "KDE, XFCE, MATE, Cinnamon, and Budgie show it natively."
