#!/usr/bin/env python3
"""Assemble the staged /usr tree shared by the .deb and .rpm builders.

Layout (prefix /usr):
  lib/dsh-linux/                  the shell (python app, assets, patches, vendor pnpm, launcher)
  share/dsh-linux/dsh/            the bundled DeepSeek Harness snapshot (with .git, no node_modules)
  share/applications/…            the desktop entry
  share/icons/hicolor/…           DeepSeek icons (22…256 px + scalable SVG)
  bin/dsh-linux -> ../lib/dsh-linux/bin/dsh-linux
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE = os.path.join(ROOT, 'build', 'stage')
SNAPSHOT = os.path.join(ROOT, 'build', 'snapshot')
VERSION = '0.1.0'
RELEASE = '1'

APP_DIR = os.path.join('usr', 'lib', 'dsh-linux')
SHARE_APP_DIR = os.path.join('usr', 'share', 'dsh-linux')


def copy_app(path):
    dest = os.path.join(STAGE, APP_DIR, path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isdir(path):
        shutil.copytree(path, dest, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'), dirs_exist_ok=True)
    else:
        shutil.copy2(path, dest)
    return dest


def install_file(src, rel):
    dest = os.path.join(STAGE, 'usr', rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)


def install_symlink(target, link):
    dest = os.path.join(STAGE, 'usr', link)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest):
        os.remove(dest)
    os.symlink(target, dest)


def main():
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)

    # The shell itself.
    for name in ('dsh_linux', 'assets', 'patches', 'vendor', 'bin'):
        copy_app(name)
    # Assets: keep only what the app and the icon theme consume.
    assets = os.path.join(STAGE, APP_DIR, 'assets')
    for junk in ('deepseek-favicon.ico',):
        path = os.path.join(assets, junk)
        if os.path.exists(path):
            os.remove(path)

    # The bundled checkout snapshot (git history included, build outputs not).
    if not os.path.isdir(os.path.join(SNAPSHOT, '.git')):
        sys.exit('build/snapshot missing; create it with packaging/make-snapshot.sh first')
    shutil.copytree(SNAPSHOT, os.path.join(STAGE, SHARE_APP_DIR, 'dsh'),
                    ignore=shutil.ignore_patterns('node_modules'))

    # Desktop entry + icons.
    install_file(os.path.join(ROOT, 'packaging', 'com.deepseek.dsh-linux.desktop'),
                 os.path.join('share', 'applications', 'com.deepseek.dsh-linux.desktop'))
    for size in (22, 24, 48, 128, 256, 512):
        install_file(
            os.path.join(ROOT, 'assets', f'icon-coloured-{size}.png'),
            os.path.join('share', 'icons', 'hicolor', f'{size}x{size}', 'apps', 'deepseek-harness.png'))
    install_file(os.path.join(ROOT, 'assets', 'icon-coloured-48.png'),
                 os.path.join('share', 'icons', 'hicolor', 'scalable', 'apps', 'deepseek-harness.png'))
    install_symlink('../lib/dsh-linux/bin/dsh-linux', os.path.join('bin', 'dsh-linux'))

    # Mode table for packaging metadata.
    print(f'staged {STAGE}')
    print(f'version {VERSION} release {RELEASE}')


if __name__ == '__main__':
    main()
