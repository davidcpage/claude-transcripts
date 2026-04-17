"""
Lazy full-text index over user-turn text in Claude Code session .jsonl files.

Each entry is keyed by absolute path with (mtime, size) as the freshness
check. Cached to an OS-appropriate user cache dir so rebuilds on startup
reprocess only new or modified files; since session files are effectively
append-only, steady-state rebuilds touch at most the active one.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _default_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "claude-transcripts"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "claude-transcripts" / "Cache"
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "claude-transcripts"


CACHE_DIR = _default_cache_dir()
CACHE_FILE = CACHE_DIR / "search-index.json"
CACHE_VERSION = 1


def _extract_user_text(filepath: Path) -> str:
    """Concatenate all user-turn text content from a .jsonl session."""
    parts = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "user":
                    continue
                msg = obj.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
    except OSError:
        pass
    return "\n".join(parts)


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != CACHE_VERSION:
            return {}
        return data.get("entries", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(entries: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": CACHE_VERSION, "entries": entries}, fh)
        tmp.replace(CACHE_FILE)
    except OSError:
        pass


def _snippet(text: str, idx: int, q_len: int, pad_before: int = 80, pad_after: int = 160) -> str:
    start = max(0, idx - pad_before)
    end = min(len(text), idx + q_len + pad_after)
    frag = text[start:end].replace("\n", " ").replace("\r", " ").strip()
    if start > 0:
        frag = "\u2026" + frag
    if end < len(text):
        frag = frag + "\u2026"
    return frag


class SearchIndex:
    def __init__(self):
        self._entries: dict = _load_cache()

    def build(self, files) -> tuple[int, float]:
        """Bring cache entries up to date for each file. Returns (rebuilt_count, seconds)."""
        t0 = time.monotonic()
        rebuilt = 0
        dirty = False
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            key = str(f)
            entry = self._entries.get(key)
            if entry and entry.get("mtime") == st.st_mtime and entry.get("size") == st.st_size:
                continue
            text = _extract_user_text(f)
            self._entries[key] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "text": text,
            }
            rebuilt += 1
            dirty = True
        if dirty:
            _save_cache(self._entries)
        return rebuilt, time.monotonic() - t0

    def search(self, query: str, files) -> dict:
        """Return {str(path): snippet} for files whose cached text contains query (case-insensitive)."""
        q = (query or "").strip()
        if not q:
            return {}
        ql = q.lower()
        results: dict[str, str] = {}
        for f in files:
            key = str(f)
            entry = self._entries.get(key)
            if not entry:
                continue
            text = entry.get("text", "")
            if not text:
                continue
            idx = text.lower().find(ql)
            if idx == -1:
                continue
            results[key] = _snippet(text, idx, len(q))
        return results
