"""
HTTP server for the Claude Code Session Viewer.

Serves a bundled index.html plus JSON endpoints for listing sessions and
streaming individual JSONL files. Three source modes:

  * default: ~/.claude/annotated-sessions/ (the annotated bucket)
  * --raw  : ~/.claude/projects/**/*.jsonl (all raw sessions, unfiltered)
  * --dir  : a specific directory of .jsonl files
"""

from __future__ import annotations

import http.server
import importlib.resources
import json
import os
import urllib.parse
from pathlib import Path

CLAUDE_BASE = Path.home() / ".claude"
RAW_DIR = CLAUDE_BASE / "projects"
ANNOTATED_DIR = CLAUDE_BASE / "annotated-sessions"

# Populated by run_server() before the HTTP server starts.
_SERVE_DIR: Path | None = None
_MODE: str = "annotated"  # "annotated" | "raw" | "dir"
_ALLOWED_DIRS: list[str] = []
_INDEX_HTML: bytes = b""


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
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_INDEX_HTML)))
            self.end_headers()
            self.wfile.write(_INDEX_HTML)
            return

        self.send_error(404, "Not found")

    def log_request(self, code="-", size="-"):
        print(f"  {code} {self.requestline}")


def _load_index_html() -> bytes:
    return (importlib.resources.files("claude_transcripts") / "index.html").read_bytes()


def run_server(port: int = 8080, raw: bool = False, directory: Path | None = None):
    """Start the viewer HTTP server. Blocks until interrupted."""
    global _SERVE_DIR, _MODE, _ALLOWED_DIRS, _INDEX_HTML

    _INDEX_HTML = _load_index_html()

    if raw and directory:
        raise ValueError("--raw and --dir are mutually exclusive")

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

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print("Claude Code Session Viewer")
    print(f"  http://localhost:{port}")
    print(f"  Serving {source_label}")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    run_server()
