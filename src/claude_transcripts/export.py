"""
Export a single session as a self-contained standalone HTML file.

The generated file embeds the session JSONL (and any annotation already
present in the file) as a JSON literal inside the bundled viewer HTML, so it
can be opened directly in a browser with no server and shared as a single
artifact.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path


INJECTION_MARKER = "  <script>\n  (function() {"


def _load_template() -> str:
    return (importlib.resources.files("claude_transcripts") / "index.html").read_text(encoding="utf-8")


def _inline_script(payload: dict) -> str:
    # json.dumps escapes control characters; we additionally escape "</" so a
    # literal "</script>" inside the session text cannot close the surrounding
    # <script> tag. "\/" is a valid JSON escape, so JSON.parse recovers "/".
    encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"  <script>window.INLINE_SESSION = {encoded};</script>\n"


def export_session(session_path: Path, output_path: Path) -> Path:
    """Read ``session_path`` and write a standalone HTML viewer to ``output_path``."""
    session_path = Path(session_path).expanduser()
    if not session_path.is_file():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    text = session_path.read_text(encoding="utf-8")
    payload = {"text": text, "filename": session_path.name}

    html = _load_template()
    if INJECTION_MARKER not in html:
        raise RuntimeError("Could not locate script injection point in index.html template")
    html = html.replace(INJECTION_MARKER, _inline_script(payload) + INJECTION_MARKER, 1)

    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
