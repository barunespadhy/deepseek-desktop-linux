#!/bin/sh
# Bundle PyGObject from the SDK's site-packages into /app so the runtime
# python can import gi (the platform runtime ships python without PyGObject).
set -e
python3 - <<'PY'
import os
import shutil
import sys

import gi

dest = f'/app/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
src = os.path.dirname(os.path.dirname(gi.__file__))
os.makedirs(dest, exist_ok=True)
copied = 0
for name in os.listdir(src):
    if name == 'gi' or name.startswith('gi.') or name.startswith('_gi') or name.startswith('pygobject'):
        path = os.path.join(src, name)
        target = os.path.join(dest, name)
        if os.path.isdir(path):
            shutil.copytree(path, target, dirs_exist_ok=True)
        else:
            shutil.copy2(path, target)
        copied += 1
print(f'bundled pygobject: {copied} entries from {src}')
PY
