# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Shared encoding utilities for tool file and subprocess output decoding.

Two decoding strategies are exposed:

* ``decode_bytes`` — for subprocess / shell output.  Tries the system locale
  encoding first (shell output often uses the active code page), then UTF-8,
  then common CJK encodings.  Never raises; falls back to a lossy UTF-8 decode.

* ``candidate_encodings`` — for reading text files.  Tries UTF-8 / UTF-8-BOM
  first (most source files), then the system locale encoding, then common CJK
  encodings.

Both helpers deduplicate the candidate list so the system locale encoding is
not tried twice when it happens to equal one of the hard-coded values.

``path_key`` returns a stable, resolved string key for a ``Path`` that is used
as a dict key to track per-file encoding / newline metadata.
"""
from __future__ import annotations

import locale
from pathlib import Path


def decode_bytes(raw: bytes | bytearray) -> str:
    """Decode subprocess/shell output without assuming UTF-8.

    Tries the system locale encoding first, then UTF-8, then common CJK
    encodings.  Falls back to a lossy UTF-8 decode so callers never receive a
    ``UnicodeDecodeError``.
    """
    candidates = [
        locale.getpreferredencoding(False),
        "utf-8",
        "gbk",
        "cp936",
    ]
    seen: set[str] = set()
    for encoding in candidates:
        if not encoding:
            continue
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return bytes(raw).decode(encoding)
        except UnicodeDecodeError:
            continue
    return bytes(raw).decode("utf-8", errors="replace")


def candidate_encodings() -> list[str]:
    """Return an ordered list of encodings to try when reading text files.

    UTF-8 variants come first (most source code is UTF-8), followed by the
    system locale encoding and common CJK fallbacks.
    """
    candidates = [
        "utf-8",
        "utf-8-sig",
        locale.getpreferredencoding(False),
        "gbk",
        "cp936",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for encoding in candidates:
        if not encoding:
            continue
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(encoding)
    return ordered


def path_key(path: Path) -> str:
    """Return a stable string key for *path*, using its resolved absolute form."""
    try:
        return str(path.resolve())
    except Exception:
        return str(path)
