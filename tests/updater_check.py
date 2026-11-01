"""Simulate an upstream release and exercise the Updater end to end."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dsh_linux.config import Config
from dsh_linux.updater import Updater

repo = sys.argv[1] if len(sys.argv) > 1 else 'updater-test'
config = Config(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
config.data['channel'] = 'rc'

events = []
updater = Updater(repo, config, config.dir, lambda msg: print(f'[log] {msg}', flush=True))
updater.on_presentation = lambda p: (events.append(dict(p)), print(f'[presentation] {p}', flush=True))
updater.restart_callback = lambda: print('[restart] server restarted (dummy)', flush=True)

print('[check] starting', flush=True)
updater.check(silent=False)
print(f'[check] presentation={updater.presentation}', flush=True)

print('[apply] starting', flush=True)
updater.apply()
updater.busy.wait() if False else None
import time
while updater.busy.is_set():
    time.sleep(1)
print(f'[apply] final presentation={updater.presentation}', flush=True)
print('[phases]', [e.get('phase') for e in events], flush=True)
