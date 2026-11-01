#!/usr/bin/env python3
"""Build the .deb from the staged tree.

Uses dpkg-deb when available; otherwise falls back to a dependency-free ar
writer (the .deb container is an `ar` archive holding debian-binary,
control.tar.xz, and data.tar.xz), so the package builds on any machine.
"""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

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
Homepage: https://github.com/barunespadhy/deepseek-desktop-linux/
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


def ar_member(name: str, payload: bytes, mode: int = 0o100644) -> bytes:
    """One `ar` archive member with a canonical 60-byte header."""
    header = ''.join((
        name[:16].ljust(16),
        '0'.ljust(12),
        '0'.ljust(6),
        '0'.ljust(6),
        f'{mode:o}'.ljust(8),
        str(len(payload)).ljust(10),
        '`\n',
    ))
    assert len(header) == 60, f'ar header must be 60 bytes (got {len(header)})'
    return header.encode('ascii') + payload


def tar_xz_dir(root: str, paths=None, skip=()) -> bytes:
    """A directory's contents as a tar.xz stream with root-owned entries."""
    with tempfile.NamedTemporaryFile(suffix='.tar.xz', delete=False) as handle:
        tmp = handle.name
    try:
        with tarfile.open(tmp, 'w:xz', format=tarfile.GNU_FORMAT) as tar:
            def add(path, arcname):
                info = tar.gettarinfo(path, arcname)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                if info.isdir():
                    info.mode = 0o755
                elif info.issym():
                    info.mode = 0o777
                elif info.mode & 0o111:
                    info.mode = 0o755
                else:
                    info.mode = 0o644
                if info.isreg():
                    with open(path, 'rb') as handle:
                        tar.addfile(info, handle)
                else:
                    tar.addfile(info)
            if paths is not None:
                for arcname in paths:
                    add(os.path.join(root, arcname), './' + arcname)
            else:
                add(root, './')
                for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                    dirnames[:] = sorted(name for name in dirnames
                                         if not (dirpath == root and name in skip))
                    filenames.sort()
                    relative = os.path.relpath(dirpath, root)
                    prefix = './' if relative == '.' else './' + relative + '/'
                    for name in dirnames:
                        add(os.path.join(dirpath, name), prefix + name + '/')
                    for name in filenames:
                        add(os.path.join(dirpath, name), prefix + name)
        with open(tmp, 'rb') as handle:
            return handle.read()
    finally:
        os.unlink(tmp)


def build_deb_standalone(root: str, output: str):
    """Assemble the .deb without dpkg-deb (ar + tar.xz containers)."""
    debian = os.path.join(root, 'DEBIAN')
    control = tar_xz_dir(debian, sorted(os.listdir(debian)))
    data = tar_xz_dir(root, skip={'DEBIAN'})
    with open(output, 'wb') as handle:
        handle.write(b'!<arch>\n')
        handle.write(ar_member('debian-binary', b'2.0\n'))
        handle.write(ar_member('control.tar.xz', control))
        handle.write(ar_member('data.tar.xz', data))


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
    if shutil.which('dpkg-deb'):
        subprocess.run(
            ['dpkg-deb', '--build', '--root-owner-group', '-Zxz', root, output],
            check=True)
    else:
        print('dpkg-deb not found; building the .deb with the built-in ar writer')
        build_deb_standalone(root, output)
    print(f'built {output}')
    if shutil.which('dpkg-deb'):
        subprocess.run(['dpkg-deb', '--info', output], check=False)
    else:
        with open(output, 'rb') as handle:
            print(f'ar magic: {handle.read(8)!r}')
    subprocess.run(['du', '-sh', output])


if __name__ == '__main__':
    main()
