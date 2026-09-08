#!/usr/bin/env python3
"""Apply the reviewed RC2 diff in a checkout and record the exact tested tree.

The human-readable source patch is SHA-256 pinned and copied to the validation
artifact. Its transfer copy is removed before packaging. No network requests,
credentials, branch movement or commits are performed by this script.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT = Path('/tmp/wn3-ci')
PATCH = ROOT / 'release' / 'wn3-stage' / 'mean-view.patch'
PATCH_SHA256 = 'c2cad886ace94bda59ab7ba8177c5198324460e65d2677cc9d4064981ec5904e'


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def main() -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    if PATCH.exists():
        raw = PATCH.read_bytes()
        if len(raw) != 29414 or hashlib.sha256(raw).hexdigest() != PATCH_SHA256:
            raise RuntimeError('Reviewed WN3 RC2 source patch SHA-256 mismatch')
        evidence = AUDIT / 'mean-view.patch'
        evidence.write_bytes(raw)
        git('apply', '--check', '--index', str(evidence))
        git('apply', '--index', str(evidence))
        git('rm', '--', str(PATCH.relative_to(ROOT)))
        print('Applied reviewed WN3 mean/ensemble views; transfer patch removed')
    git('diff', '--cached', '--check')
    tree = git('write-tree')
    (AUDIT / 'TREE_SHA').write_text(tree + '\n', encoding='utf-8')
    print('Candidate tree:', tree)


if __name__ == '__main__':
    main()
