#!/usr/bin/env python3
"""Build the .deb from the staged tree (no root needed; dpkg-deb is enough)."""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE = os.path.join(ROOT, 'build', 'stage')
DIST = os.path.join(ROOT, 'dist')
VERSION = '0.1.0'
RELEASE = '1'
ARCH = 'amd64'
PACKAGE = 'deepseek-harness-linux'

CONTROL = f"""Package: {PACKAGE}
Version: {VERSION}-{RELEASE}
Section: devel
Priority: optional
Architecture: {ARCH}
Maintainer: DeepSeek Harness Linux <dsh-linux@localhost>
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-webkit-6.0, gir1.2-gdkpixbuf-2.0, git, ca-certificates
Suggests: nodejs (>= 24.9)
Description: DeepSeek Harness desktop for Linux
 Lightweight GTK4 + WebKitGTK desktop shell for the DeepSeek Harness agent
 harness, with a splash screen, status-area app indicator, appearance
 settings (corner radius and custom CSS), and git-based self-updates with
 Release-candidate and Beta channels.
 .
 Node.js >= 24.9 is required at runtime and is resolved from nvm/volta/asdf
 locations when not on the desktop PATH; pnpm is bundled.
"""

POSTINST = """#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
"""

POSTRM = POSTINST


def installed_kb():
    total = 0
    for dirpath, _dirnames, filenames in os.walk(STAGE):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            total += os.path.getsize(path)
    return max(1, total // 1024)


def main():
    if not os.path.isdir(STAGE):
        sys.exit('run packaging/stage.py first')
    root = os.path.join(ROOT, 'build', 'deb-root')
    if os.path.isdir(root):
        shutil.rmtree(root)
    shutil.copytree(STAGE, root, symlinks=True)
    debian = os.path.join(root, 'DEBIAN')
    os.makedirs(debian)
    control = CONTROL + f'Installed-Size: {installed_kb()}\n'
    with open(os.path.join(debian, 'control'), 'w') as handle:
        handle.write(control)
    for name, script in (('postinst', POSTINST), ('postrm', POSTRM)):
        path = os.path.join(debian, name)
        with open(path, 'w') as handle:
            handle.write(script)
        os.chmod(path, 0o755)
    os.makedirs(DIST, exist_ok=True)
    output = os.path.join(DIST, f'{PACKAGE}_{VERSION}-{RELEASE}_{ARCH}.deb')
    subprocess.run(
        ['dpkg-deb', '--build', '--root-owner-group', '-Zxz', root, output],
        check=True)
    print(f'built {output}')
    subprocess.run(['dpkg-deb', '--info', output], check=False)
    subprocess.run(['du', '-sh', output])


if __name__ == '__main__':
    main()
