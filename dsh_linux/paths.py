"""Tool discovery for desktop-launched environments.

GNOME/KDE launch apps with a minimal PATH that misses per-shell additions
(nvm, volta, asdf, fnm, ~/.local/bin, …). The shell resolves `node`, `pnpm`,
`corepack`, and `git` itself: first from an explicit override environment
variable, then from a search path that folds those common locations into the
session PATH, and finally from the raw PATH as a last resort.
"""

import glob
import os
import shutil

TOOL_OVERRIDES = {
    'node': 'DSH_LINUX_NODE',
    'pnpm': 'DSH_LINUX_PNPM',
    'corepack': 'DSH_LINUX_COREPACK',
    'git': 'DSH_LINUX_GIT',
}


def search_paths():
    """The PATH plus the per-user locations version managers install into."""
    paths = [entry for entry in os.environ.get('PATH', '').split(os.pathsep) if entry]
    home = os.path.expanduser('~')
    # nvm: every version dir plus the `current` alias, newest first.
    nvm = sorted(
        glob.glob(os.path.join(home, '.nvm', 'versions', 'node', '*', 'bin')),
        reverse=True,
    )
    extras = [
        os.path.join(home, '.nvm', 'current', 'bin'),
        os.path.join(home, '.volta', 'bin'),
        os.path.join(home, '.asdf', 'shims'),
        os.path.join(home, '.asdf', 'bin'),
        os.path.join(home, '.local', 'share', 'mise', 'shims'),
        os.path.join(home, '.local', 'share', 'fnm'),
        os.path.join(home, '.local', 'bin'),
        os.path.join(home, 'bin'),
        os.path.join(home, '.nix-profile', 'bin'),
        '/opt/node/bin',
        '/usr/local/bin',
        '/usr/bin',
        '/bin',
    ]
    return nvm + extras + paths


def find_program(tool: str):
    """Resolve one tool to an absolute executable path, or None."""
    env_name = TOOL_OVERRIDES.get(tool)
    if env_name:
        override = os.environ.get(env_name)
        if override:
            candidate = os.path.expanduser(override.strip())
            # Node-run scripts (pnpm.cjs/mjs) need no execute bit.
            if os.path.isfile(candidate) and (
                    candidate.endswith(('.js', '.mjs', '.cjs')) or os.access(candidate, os.X_OK)):
                return candidate
    names = [tool]
    if tool == 'node':
        names = ['node', 'nodejs']
    for directory in search_paths():
        for name in names:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    found = shutil.which(tool)
    return found if found else None


def augment_path():
    """A PATH for child processes that keeps the discovered locations last."""
    extra = [directory for directory in search_paths()
             if directory not in os.environ.get('PATH', '').split(os.pathsep)]
    return os.pathsep.join([os.environ.get('PATH', ''), *extra])
