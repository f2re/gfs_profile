#!/usr/bin/env python3
"""Apply the reviewed source diff in a real checkout, then remove delivery files.

The compressed transfer is SHA-256 pinned. The plain diff and exact candidate
Git tree remain in validation artifacts. No network, credentials or commits.
"""
from __future__ import annotations
import gzip
import hashlib
import io
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path('/tmp/wn3-off')
PARTS = [f'maintenance/disable-wn3/part-{i}.bin' for i in range(3)]
TRANSFER = [*PARTS, '.github/scripts/apply_disable_wn3.py', '.github/workflows/disable-wn3.yml']
PACKED_SHA256 = 'a2673e44c52678e2bb05ed8d39dbce25ec88cf38a98717585dff13571224ea6a'
PATCH_SHA256 = '35d660809cbf579e260ca916e7778dc1a7695ed2be7059de5bfac40fe1afed72'


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def main() -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    packed = b''.join((ROOT / path).read_bytes() for path in PARTS)
    if len(packed) != 18456 or hashlib.sha256(packed).hexdigest() != PACKED_SHA256:
        raise RuntimeError('Reviewed source transfer SHA-256 mismatch')
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as handle:
        patch = handle.read(80086)
    if len(patch) != 80085 or hashlib.sha256(patch).hexdigest() != PATCH_SHA256:
        raise RuntimeError('Reviewed source diff SHA-256 mismatch')
    path = AUDIT / 'disable-weathernext.patch'
    path.write_bytes(patch)
    git('apply', '--check', '--index', str(path))
    git('apply', '--index', str(path))
    git('rm', '--', *TRANSFER)
    git('diff', '--cached', '--check')
    tree = git('write-tree')
    (AUDIT / 'TREE_SHA').write_text(tree + '\n')
    print('Candidate tree:', tree)


if __name__ == '__main__':
    main()
