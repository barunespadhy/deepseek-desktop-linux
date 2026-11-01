"""Repair native addons whose ELF headers demand an executable stack.

The bundled C compiler (vendor/cc, a TinyCC wrapper) emits shared objects
without a ``PT_GNU_STACK`` program header.  Loaders then assume the object
needs an executable stack, and glibc >= 2.43 refuses to enable one for
``dlopen``'ed objects (``EINVAL``), which surfaces as

    …/system.node: cannot enable executable stack as shared object requires:
    Invalid argument

and breaks both the chat turn pipeline and the session-log write lease (and
therefore the ``/api/session.export`` ZIP download) in the DeepSeek Harness
web server.  This module rewrites the ELF file to carry an explicit
``PT_GNU_STACK`` with ``PF_R|PF_W`` (no execute), which is exactly what a
correctly linked addon carries.

The rewrite appends one 56-byte program-header entry at the end of the file
and points ``e_phoff``/``e_phnum`` at it — the layout the kernel and glibc
already support for objects whose header table is not file-mapped.  It is
idempotent and keeps the original bytes untouched otherwise.
"""

import os
import struct

PT_GNU_STACK = 0x6474E551
PF_R, PF_W, PF_X = 0x4, 0x2, 0x1

# ELF64 little-endian header field offsets.
ELF_EI_CLASS = 4
ELF_EI_DATA = 5
ELF_E_PHOFF = 0x20
ELF_E_PHNUM = 0x38

_PHDR64 = struct.Struct('<IIQQQQQQ')


def _elf64_le(data: bytes) -> bool:
    return len(data) >= 64 and data[:4] == b'\x7fELF' \
        and data[ELF_EI_CLASS] == 2 and data[ELF_EI_DATA] == 1


def gnu_stack_flags(data: bytes):
    """The ``PT_GNU_STACK`` ``p_flags``, or ``None`` when the header is absent."""
    if not _elf64_le(data):
        return None
    e_phoff = struct.unpack_from('<Q', data, ELF_E_PHOFF)[0]
    e_phentsize = struct.unpack_from('<H', data, 0x36)[0]
    e_phnum = struct.unpack_from('<H', data, ELF_E_PHNUM)[0]
    if e_phentsize != 56:
        return None
    for index in range(e_phnum):
        offset = e_phoff + index * 56
        if offset + 56 > len(data):
            return None
        p_type, p_flags = _PHDR64.unpack_from(data, offset)[:2]
        if p_type == PT_GNU_STACK:
            return p_flags
    return None


def repair_execstack(data: bytes):
    """Return ``(new_bytes, action)``; ``action`` is None when already safe.

    ``action`` is a short human-readable description otherwise.  Unsupported
    or unparsable files are returned unchanged with a ``None`` action.
    """
    if not _elf64_le(data):
        return data, None
    flags = gnu_stack_flags(data)
    if flags is None:
        action = 'added PT_GNU_STACK (RW)'
    elif flags & PF_X:
        action = 'cleared executable-stack flag (RW)'
    else:
        return data, None
    e_phoff = struct.unpack_from('<Q', data, ELF_E_PHOFF)[0]
    e_phentsize = struct.unpack_from('<H', data, 0x36)[0]
    e_phnum = struct.unpack_from('<H', data, ELF_E_PHNUM)[0]
    if e_phentsize != 56 or e_phoff + e_phnum * 56 > len(data):
        return data, None
    # Keep the original program-header entries and append one canonical
    # GNU_STACK entry (RW, empty, 16-byte aligned) as a relocated table at
    # the end of the file; e_phoff/e_phnum point at the new table.
    entries = data[e_phoff:e_phoff + e_phnum * 56]
    entry = _PHDR64.pack(PT_GNU_STACK, PF_R | PF_W, 0, 0, 0, 0, 0, 0x10)
    new = bytearray(data)
    new += entries + entry
    new[ELF_E_PHOFF:ELF_E_PHOFF + 8] = struct.pack('<Q', len(data))
    new[ELF_E_PHNUM:ELF_E_PHNUM + 2] = struct.pack('<H', e_phnum + 1)
    return bytes(new), action


def fix_execstack_file(path: str, log) -> str | None:
    """Repair one file in place when needed; returns the action or None."""
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError as error:
        log(f'elf-fix: cannot read {path}: {error}')
        return None
    if data[:4] != b'\x7fELF':
        return None
    new, action = repair_execstack(data)
    if action is None:
        return None
    try:
        with open(path, 'wb') as handle:
            handle.write(new)
    except OSError as error:
        log(f'elf-fix: cannot write {path}: {error}')
        return None
    log(f'elf-fix: {path}: {action}')
    return action


def fix_native_addons(repo: str, log) -> int:
    """Repair every native addon/launcher of the tracked checkout.

    Covers the git-tracked package tree (first-run builds) and the
    node_modules copies an installed dependency may resolve instead.
    Returns the number of files changed.
    """
    roots = [
        os.path.join(repo, 'native', 'system', 'packages'),
        os.path.join(repo, 'node_modules', '@deepseek-ai'),
    ]
    changed = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for directory, _subdirs, files in os.walk(root):
            if 'bin' not in directory.split(os.sep):
                continue
            for name in files:
                if fix_execstack_file(os.path.join(directory, name), log):
                    changed += 1
    return changed
