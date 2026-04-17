"""
HTTP server for the Claude Code Session Viewer.

Serves a bundled index.html plus JSON endpoints for listing sessions and
streaming individual JSONL files. Three source modes:

  * default: ~/.claude/annotated-sessions/ (the annotated bucket)
  * --raw  : ~/.claude/projects/**/*.jsonl (all raw sessions, unfiltered)
  * --dir  : a specific directory of .jsonl files
"""

from __future__ import annotations

import errno
import http.server
import importlib.resources
import json
import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from .search_index import SearchIndex

CLAUDE_BASE = Path.home() / ".claude"
RAW_DIR = CLAUDE_BASE / "projects"
ANNOTATED_DIR = CLAUDE_BASE / "annotated-sessions"

# Populated by run_server() before the HTTP server starts.
_SERVE_DIR: Path | None = None
_MODE: str = "annotated"  # "annotated" | "raw" | "dir"
_ALLOWED_DIRS: list[str] = []
_SEARCH_INDEX: SearchIndex | None = None


def _read_annotation(filepath: Path):
    """Read annotation metadata from the last few lines of a JSONL file."""
    try:
        with open(filepath, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            tail = fh.read().decode("utf-8", errors="replace")
        for line in reversed(tail.strip().split("\n")):
            try:
                obj = json.loads(line)
                if obj.get("type") == "annotation":
                    return obj
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return None


def _read_session_head(filepath: Path, max_lines: int = 30):
    """Read slug, date, and first user message from the first lines of a JSONL file."""
    slug = ""
    date = ""
    preview = ""
    try:
        with open(filepath, "r") as fh:
            for i, line in enumerate(fh):
                if i > max_lines:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("slug") and not slug:
                    slug = obj["slug"]
                if obj.get("timestamp") and not date:
                    date = obj["timestamp"][:10]
                if not preview and obj.get("type") == "user":
                    msg = obj.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        preview = content[:500]
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                preview = block.get("text", "")[:500]
                                break
    except OSError:
        pass
    return slug, date, preview


def _annotation_summary(ann: dict) -> str:
    """Flatten section titles + summaries into a single searchable blob."""
    parts = []
    for sec in ann.get("sections", []) or []:
        if sec.get("title"):
            parts.append(sec["title"])
        if sec.get("summary"):
            parts.append(sec["summary"])
    return " ".join(parts)


def _annotated_sessions(directory: Path):
    """List annotated sessions under a directory."""
    sessions = []
    if not directory.exists():
        return sessions
    for f in sorted(directory.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        ann = _read_annotation(f)
        if not ann:
            continue
        slug, date, _ = _read_session_head(f, max_lines=10)
        source = ann.get("source", {})
        sessions.append({
            "file": str(f),
            "title": ann.get("title", f.stem),
            "date": date,
            "tags": ann.get("tags", []),
            "author": ann.get("author", ""),
            "slug": source.get("slug", "") or slug,
            "project": source.get("project", ""),
            "sections": len(ann.get("sections", [])),
            "summary": _annotation_summary(ann),
            "model": ann.get("generated", {}).get("model", ""),
        })
    return sessions


def _raw_sessions(root: Path):
    """List raw (unannotated) sessions under ~/.claude/projects/."""
    sessions = []
    if not root.exists():
        return sessions
    for proj_dir in sorted(root.iterdir()):
        if not proj_dir.is_dir():
            continue
        proj_name = proj_dir.name
        for f in sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.stem.endswith(".usage"):
                continue
            slug, date, preview = _read_session_head(f)
            sessions.append({
                "file": str(f),
                "title": slug or f.stem[:8],
                "date": date,
                "tags": [],
                "author": "",
                "slug": slug,
                "preview": preview,
                "project": proj_name,
                "sections": 0,
            })
    return sessions


def _dir_sessions(directory: Path):
    """List arbitrary .jsonl sessions in a directory, auto-detecting annotation."""
    sessions = []
    if not directory.exists():
        return sessions
    for f in sorted(directory.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        ann = _read_annotation(f)
        slug, date, preview = _read_session_head(f)
        if ann:
            source = ann.get("source", {})
            sessions.append({
                "file": str(f),
                "title": ann.get("title", f.stem),
                "date": date,
                "tags": ann.get("tags", []),
                "author": ann.get("author", ""),
                "slug": source.get("slug", "") or slug,
                "project": source.get("project", ""),
                "sections": len(ann.get("sections", [])),
                "summary": _annotation_summary(ann),
                "model": ann.get("generated", {}).get("model", ""),
            })
        else:
            sessions.append({
                "file": str(f),
                "title": slug or f.stem[:8],
                "date": date,
                "tags": [],
                "author": "",
                "slug": slug,
                "preview": preview,
                "project": "",
                "sections": 0,
            })
    return sessions


def get_sessions():
    """Collect sessions for the active source mode."""
    if _MODE == "raw":
        sessions = _raw_sessions(RAW_DIR)
    elif _MODE == "dir":
        sessions = _dir_sessions(_SERVE_DIR)
    else:
        sessions = _annotated_sessions(_SERVE_DIR)
    sessions.sort(key=lambda s: s.get("date", ""), reverse=True)
    return sessions


def _files_for_mode() -> list[Path]:
    """Enumerate all indexable .jsonl paths for the active mode."""
    files: list[Path] = []
    if _MODE == "raw":
        root = RAW_DIR
        if root.exists():
            for proj in root.iterdir():
                if not proj.is_dir():
                    continue
                for f in proj.glob("*.jsonl"):
                    if f.stem.endswith(".usage"):
                        continue
                    files.append(f)
    else:
        if _SERVE_DIR and _SERVE_DIR.exists():
            for f in _SERVE_DIR.rglob("*.jsonl"):
                if f.stem.endswith(".usage"):
                    continue
                files.append(f)
    return files


def _run_search(query: str) -> dict:
    """Ensure the index is fresh for the active mode and return matches."""
    global _SEARCH_INDEX
    if _SEARCH_INDEX is None:
        _SEARCH_INDEX = SearchIndex()
    files = _files_for_mode()
    rebuilt, elapsed = _SEARCH_INDEX.build(files)
    if rebuilt:
        print(f"  search index: processed {rebuilt} session(s) in {elapsed:.1f}s")
    return _SEARCH_INDEX.search(query, files)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/sessions":
            data = {"sessions": get_sessions()}
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            q = qs.get("q", [""])[0]
            matches = _run_search(q) if q else {}
            body = json.dumps({"matches": matches}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/session":
            qs = urllib.parse.parse_qs(parsed.query)
            file_path = qs.get("path", [""])[0]
            if not file_path or not os.path.isfile(file_path):
                self.send_error(404, "Session not found")
                return
            real = os.path.realpath(file_path)
            if not any(real == d or real.startswith(d + os.sep) for d in _ALLOWED_DIRS):
                self.send_error(403, "Access denied")
                return
            with open(file_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/", "/index.html"):
            body = _load_index_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404, "Not found")

    def log_request(self, code="-", size="-"):
        print(f"  {code} {self.requestline}")


def _load_index_html() -> bytes:
    return (importlib.resources.files("claude_transcripts") / "index.html").read_bytes()


DEFAULT_PORT = 8080


def run_server(port: int | None = None, raw: bool = False, directory: Path | None = None, open_browser: bool = True):
    """Start the viewer HTTP server. Blocks until interrupted.

    When ``port`` is None the default port is tried first; if that port is
    busy the OS picks a free one. An explicit port is never silently changed.
    """
    global _SERVE_DIR, _MODE, _ALLOWED_DIRS

    if raw and directory:
        raise ValueError("--raw and --dir are mutually exclusive")

    allow_fallback = port is None
    if port is None:
        port = DEFAULT_PORT

    if directory:
        _MODE = "dir"
        _SERVE_DIR = Path(directory).resolve()
        _ALLOWED_DIRS = [os.path.realpath(str(_SERVE_DIR))]
        source_label = f"directory {_SERVE_DIR}"
    elif raw:
        _MODE = "raw"
        _SERVE_DIR = RAW_DIR
        _ALLOWED_DIRS = [os.path.realpath(str(RAW_DIR))]
        source_label = f"raw sessions from {RAW_DIR}"
    else:
        _MODE = "annotated"
        _SERVE_DIR = ANNOTATED_DIR
        _ALLOWED_DIRS = [os.path.realpath(str(ANNOTATED_DIR))]
        source_label = f"annotated sessions from {ANNOTATED_DIR}"

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            if allow_fallback:
                print(f"Port {port} is in use — picking a free port instead.")
                server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
                port = server.server_address[1]
            else:
                print(f"Port {port} is already in use.", file=sys.stderr)
                print(f"Another process is listening there — stop it, or pick a different port with --port.", file=sys.stderr)
                raise SystemExit(1)
        else:
            raise
    url = f"http://localhost:{port}"
    print("Claude Code Session Viewer")
    print(f"  {url}")
    print(f"  Serving {source_label}")
    print()
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    run_server()
