#!/usr/bin/env python3
"""Build the .rpm with rpmbuild (native, or the provisioned local toolchain).

Runs the staged /usr tree through a minimal spec; on machines without rpmbuild
the packaging/fetch-rpm-tools.sh provisioned prefix under build/rpm-tools is
used automatically (with its own macros, build dirs, and tmp dir).
"""

import os
import shutil
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE = os.path.join(ROOT, 'build', 'stage')
DIST = os.path.join(ROOT, 'dist')
TOOLS = os.path.join(ROOT, 'build', 'rpm-tools')

NAME = 'deepseek-harness-linux'
VERSION = '0.1.0'
RELEASE = '1'
ARCH = 'x86_64'

SPEC = f"""Name: {NAME}
Version: {VERSION}
Release: {RELEASE}
Summary: DeepSeek Harness desktop for Linux
License: MIT
URL: https://github.com/deepseek-ai/deepseek-harness
BuildArch: {ARCH}

Requires: python3 >= 3.10
Requires: python3-gobject
Requires: gtk4
Requires: git
Recommends: webkit2gtk4.1
Recommends: nodejs >= 24.9

%description
Lightweight GTK4 + WebKitGTK desktop shell for the DeepSeek Harness agent
harness, with a splash screen, status-area app indicator, appearance settings
(corner radius and custom CSS), and git-based self-updates with
Release-candidate and Beta channels.

Node.js >= 24.9 is required at runtime and is resolved from nvm/volta/asdf
locations when not on the desktop PATH; pnpm is bundled.

Fedora: install webkit2gtk4.1. openSUSE: install the WebKitGTK 6.0 runtime
for your release.

%prep
%build
%install
rm -rf %{{buildroot}}
mkdir -p %{{buildroot}}
tar -xzf %{{_sourcedir}}/stage.tar.gz -C %{{buildroot}} --strip-components=1

%post
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi

%files
/usr/bin/dsh-linux
/usr/lib/dsh-linux
/usr/share/dsh-linux
/usr/share/applications/com.deepseek.dsh-linux.desktop
/usr/share/icons/hicolor
"""


def run(cmd, env=None):
    print('+', ' '.join(cmd))
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout[-1200:])
    if result.returncode != 0:
        print(result.stderr[-1200:], file=sys.stderr)
        sys.exit(f'rpmbuild failed (exit {result.returncode})')
    return result


def main():
    if not os.path.isdir(STAGE):
        sys.exit('run packaging/stage.py first')
    topdir = os.path.join(TOOLS, 'rpbt')
    for sub in ('BUILD', 'RPMS', 'SOURCES', 'SPECS', 'SRPMS', 'BUILDROOT'):
        os.makedirs(os.path.join(topdir, sub), exist_ok=True)

    # Stage → source archive for %{_sourcedir}.
    with tarfile.open(os.path.join(topdir, 'SOURCES', 'stage.tar.gz'), 'w:gz') as tar:
        tar.add(STAGE, arcname='stage')
    with open(os.path.join(topdir, 'SPECS', f'{NAME}.spec'), 'w') as handle:
        handle.write(SPEC)

    env = dict(os.environ)
    env['TMPDIR'] = os.path.join(TOOLS, 'tmp')
    env['TMP'] = env['TMPDIR']
    os.makedirs(env['TMPDIR'], exist_ok=True)

    native = shutil.which('rpmbuild')
    local = os.path.join(TOOLS, 'root', 'usr', 'bin', 'rpmbuild')
    lib = os.path.join(TOOLS, 'root', 'usr', 'lib', 'x86_64-linux-gnu')
    combined = os.path.join(TOOLS, 'combined.macros')
    if native:
        base = [native, '--define', f'_topdir {topdir}', '--nodeps', '-bb',
                os.path.join(topdir, 'SPECS', f'{NAME}.spec')]
        run(base, env)
    elif os.path.exists(local):
        env['LD_LIBRARY_PATH'] = lib + ':' + env.get('LD_LIBRARY_PATH', '')
        with open(combined, 'w') as handle:
            with open(os.path.join(TOOLS, 'root', 'usr', 'lib', 'rpm', 'macros')) as source:
                handle.write(source.read())
            with open(os.path.join(TOOLS, 'macros.override')) as source:
                handle.write(source.read())
        run([local, '--rcfile', os.path.join(TOOLS, 'my-rpmrc'),
             '--macros', combined, '--dbpath', os.path.join(TOOLS, 'db'),
             '--nodeps', '-bb', os.path.join(topdir, 'SPECS', f'{NAME}.spec')], env)
    else:
        sys.exit('rpmbuild not found; run packaging/fetch-rpm-tools.sh first')

    built = os.path.join(topdir, 'RPMS', ARCH, f'{NAME}-{VERSION}-{RELEASE}.{ARCH}.rpm')
    if not os.path.exists(built):
        sys.exit('rpmbuild produced no package')
    os.makedirs(DIST, exist_ok=True)
    output = os.path.join(DIST, f'{NAME}-{VERSION}-{RELEASE}.{ARCH}.rpm')
    shutil.copy2(built, output)
    print(f'built {output}')
    print(f'size: {os.path.getsize(output) / 1048576:.1f} MiB')

    # Validate with a local rpm when possible.
    rpm = shutil.which('rpm')
    if not rpm and os.path.exists(os.path.join(TOOLS, 'root', 'usr', 'bin', 'rpm')):
        rpm = os.path.join(TOOLS, 'root', 'usr', 'bin', 'rpm')
    if rpm:
        venv = dict(env)
        if not shutil.which('rpm'):
            venv['LD_LIBRARY_PATH'] = lib + ':' + venv.get('LD_LIBRARY_PATH', '')
            query = [rpm, '--rcfile', os.path.join(TOOLS, 'my-rpmrc'), '--macros', combined,
                     '--dbpath', os.path.join(TOOLS, 'db')]
        else:
            query = [rpm]
        check = subprocess.run(query + ['-q', '-p', output], env=venv, capture_output=True, text=True)
        print('rpm -q -p:', check.stdout.strip() or check.stderr.strip())
        files = subprocess.run(query + ['-q', '-l', '-p', output], env=venv, capture_output=True, text=True)
        print(f'rpm file count: {len(files.stdout.splitlines())}')
        if files.returncode != 0:
            print(files.stderr[-400:], file=sys.stderr)


if __name__ == '__main__':
    main()
