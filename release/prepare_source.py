#!/usr/bin/env python3
"""Apply the reviewed offline diff to a checkout, then record the tested Git tree.

The SHA-256 values pin one specific source patch. The transfer files are removed
from the release tree, and the decoded diff is kept in the CI audit artifact.
No network calls, credential handling, branch changes, or commits happen here.
"""
from __future__ import annotations

import hashlib
import lzma
import subprocess
from pathlib import Path

COMPRESSED_SHA256 = "aece5bd19cb1f9695233649f8dd385ebf8c0009b5107ed4878d70736dbb37906"
PATCH_SHA256 = "c179b4b5e61c84fb7712a09951b6ef6c9556e6aed7309a5ce1678b25d73efe54"
ROOT = Path(__file__).resolve().parent.parent
AUDIT = Path("/tmp/wn3-ci")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / "release" / "wn3-stage" / f"part-{i}.bin" for i in range(6)]
    if any(path.exists() for path in paths):
        if not all(path.is_file() for path in paths):
            raise RuntimeError("Incomplete reviewed source transfer")
        packed = b"".join(path.read_bytes() for path in paths)
        if len(packed) != 56416 or hashlib.sha256(packed).hexdigest() != COMPRESSED_SHA256:
            raise RuntimeError("Source transfer SHA-256 mismatch")
        patch = lzma.decompress(packed, memlimit=256 * 1024 * 1024)
        if len(patch) != 256668 or hashlib.sha256(patch).hexdigest() != PATCH_SHA256:
            raise RuntimeError("Reviewed patch SHA-256 mismatch")
        decoded = AUDIT / "reviewed-source.patch"
        decoded.write_bytes(patch)
        git("apply", "--check", "--index", str(decoded))
        git("apply", "--index", str(decoded))
        git("rm", "--", *(str(path.relative_to(ROOT)) for path in paths))
        print("Applied the SHA-256-pinned source diff; transfer files removed from release tree")
    git("diff", "--cached", "--check")
    tree = git("write-tree")
    (AUDIT / "TREE_SHA").write_text(tree + "\n", encoding="utf-8")
    print("Candidate tree:", tree)


if __name__ == "__main__":
    main()
