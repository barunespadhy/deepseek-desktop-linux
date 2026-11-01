# DeepSeek Harness for Linux

A tiny desktop shell that wraps [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
into a normal Linux application: double-click the launcher, watch the splash, and the full
DeepSeek Harness interface opens in its own window — the same sessions, settings, and plugins
you use from the CLI.

## Features

- **One-click desktop app**: launch it like any application; no terminals, no manual `dsh web`.
- **Self-provisioning**: the first launch copies the bundled DeepSeek Harness, installs its
  dependencies, and builds the interface while the splash screen shows progress. Later launches
  start instantly.
- **Built-in updates**: git-based updating with a Release-candidate/Beta channel. No manual setup needed.
- **Appearance**: Corner-radius control and custom CSS in Settings → Appearance.
- Shares `~/.dsh` with the CLI (same sessions and settings).

## Installation

Pick the latest package for your distribution from the [releases](https://github.com/barunespadhy/deepseek-desktop-linux/releases) section.

### Debian / Ubuntu and derivatives (.deb)

```sh
sudo apt install ./dist/deepseek-harness-linux_0.1.0-1_amd64.deb
```

Dependencies are resolved automatically by the package manager:

| Package | Purpose |
|---|---|
| `python3` (≥ 3.10) | The shell runtime |
| `python3-gi` | GTK bindings |
| `gir1.2-gtk-4.0` | GTK 4 toolkit |
| `gir1.2-webkit-6.0` | The WebKitGTK 6.0 webview |
| `gir1.2-gdkpixbuf-2.0` | Image loading |
| `git` | Update checks and application |
| `ca-certificates` | HTTPS for updates and installs |
| Node.js ≥ 24.9 | Provided by you — see below |

### Fedora (.rpm)

```sh
sudo dnf install ./dist/deepseek-harness-linux-0.1.0-1.x86_64.rpm
```

Dependencies (resolved by dnf): `python3` ≥ 3.10, `python3-gobject`, `gtk4`, `git`;
`webkit2gtk4.1` (WebKitGTK 6.0) and Node.js ≥ 24.9 are recommended.

### openSUSE (.rpm)

```sh
sudo zypper install ./dist/deepseek-harness-linux-0.1.0-1.x86_64.rpm
```

### Any other Linux distribution (.tar.gz)

The portable tarball for other linux distributions:

```sh
tar -xzf dist/deepseek-harness-linux-0.1.0-x86_64.tar.gz
cd deepseek-harness-linux
./install.sh        # per-user install: icons, desktop entry, ~/.local/bin/dsh-linux
dsh-linux           # …or run ./bin/dsh-linux in place
```

`install.sh` installs into your user profile (no root needed). Install these packages first:

- **Debian/Ubuntu:** `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0 gir1.2-gdkpixbuf-2.0 git`
- **Fedora:** `sudo dnf install python3-gobject gtk4 webkitgtk6.0 git`
- **Arch:** `sudo pacman -S python-gobject gtk4 webkitgtk-6.0 git`
- **openSUSE:** `sudo zypper install python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-WebKit-6_0 git`

### Node.js ≥ 24.9

The .deb and .rpm keep Node.js user-managed because distro versions are usually too old.
Install it any way you like:

```sh
# nvm (recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
nvm install 24
```

The app auto-discovers Node from nvm, volta, asdf, mise, fnm, `~/.local/bin`, `/opt/node`,
`/usr/local/bin`, and `/usr/bin` — including when it is launched from the desktop environment
(where the shell PATH is not your login shell's). pnpm and the compiler are bundled.

## Building from source

```sh
git clone https://github.com/deepseek-ai/deepseek-harness-linux.git
cd deepseek-harness-linux
mkdir -p third_party
git clone https://github.com/deepseek-ai/deepseek-harness.git third_party/dsh
./install.sh        # icons, desktop entry, ~/.local/bin/dsh-linux
dsh-linux
```

The upstream DeepSeek Harness checkout lives in `third_party/dsh/` (git-ignored) and
carries this project's UI additions (Appearance/Updates sections) as patch files in
`patches/`, applied automatically on the first run. Requirements: Python ≥ 3.10 with
PyGObject, GTK 4, WebKitGTK 6.0, git, and Node.js ≥ 24.9.

### Building the packages

```sh
packaging/fetch-vendor.sh        # bundle pnpm + the C compiler into vendor/ (git-ignored)
packaging/make-snapshot.sh       # refresh build/snapshot (needs the third_party/dsh checkout)
python3 packaging/stage.py       # assemble the shared tree into build/stage
python3 packaging/build-deb.py        # -> dist/deepseek-harness-linux_0.1.0-1_amd64.deb
python3 packaging/build-rpm.py        # -> dist/deepseek-harness-linux-0.1.0-1.x86_64.rpm
packaging/build-tar.sh                # -> dist/deepseek-harness-linux-0.1.0-x86_64.tar.gz
```

- `fetch-vendor.sh` downloads the pinned pnpm release and builds the bundled C compiler;
  every package format embeds them, and first-run builds/updates use them.
- `make-snapshot.sh` clones the git history + tracked tree from `third_party/dsh`.
- `build-deb.py` needs `dpkg-deb`.
- `build-rpm.py` uses the system `rpmbuild` when present; on Debian/Ubuntu machines without
  rpm tooling, run `packaging/fetch-rpm-tools.sh` once to provision a local rpmbuild prefix.
- `build-tar.sh` assembles the portable tree from the workspace and the snapshot.

## Notes

- The corner-radius control scales the shared `--dsw-radius-*` tokens, which every button,
  input, card, and panel corner reads, applied with an important tier plus inline overrides
  so the page's own styles can never win it back. The custom CSS editor injects a
  keep-last stylesheet, so equal-specificity rules take effect without needing `!important`.
- The splash screen follows the DeepSeek UI theme: the app samples the interface's actual
  background and label colors each session (and every minute) and uses them on the next
  launch, falling back to the system color scheme on first run.
