#!/usr/bin/env python3
"""
Helper for the session-annotator subagent.

Supports three subcommands used by the subagent pipeline:

    python3 annotate.py --resolve <slug>
        Resolve a what-three-words slug under ~/.claude/projects/ and print
        the matching session path.

    python3 annotate.py --compress-only <path-to-session.jsonl>
        Emit JSON with turn_count, compressed text, source info, and date.
        The subagent reads this and analyzes the compressed text.

    echo '<json>' | python3 annotate.py <path-to-session.jsonl> --write-annotation
        Read the subagent-generated annotation JSON from stdin and write an
        annotated copy to ~/.claude/annotated-sessions/ (override with --out).

Stdlib only. No pip install required.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path.home() / ".claude" / "annotated-sessions"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
COMPRESSED_TMP_DIR = Path("/tmp/claude-transcripts")


def parse_jsonl(path):
    raw_lines = []
    events = []
    with open(path) as f:
        for line in f:
            raw_lines.append(line)
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return raw_lines, events


def merge_events(events):
    """Merge streaming assistant chunks that share a message.id."""
    merged = []
    for ev in events:
        if ev.get("type") not in ("user", "assistant"):
            continue
        msg = ev.get("message", {})
        msg_id = msg.get("id")

        content = msg.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        msg["content"] = content

        if (ev["type"] == "assistant" and msg_id and merged
                and merged[-1].get("type") == "assistant"
                and merged[-1].get("message", {}).get("id") == msg_id):
            existing = merged[-1]["message"]
            existing["content"] = existing.get("content", []) + content
            if msg.get("usage"):
                existing["usage"] = msg["usage"]
            if msg.get("stop_reason"):
                existing["stop_reason"] = msg["stop_reason"]
        else:
            merged.append(ev)
    return merged


def compress_for_prompt(turns, start_index=0, end_index=None):
    """Compress merged turns into a prompt-friendly text with turn indices."""
    if end_index is None:
        end_index = len(turns)

    lines = []
    for i, ev in enumerate(turns[start_index:end_index], start=start_index):
        role = ev.get("type", "unknown")
        msg = ev.get("message", {})
        content = msg.get("content", [])

        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "thinking":
                thinking = block.get("thinking", "")
                if len(thinking) > 400:
                    parts.append(f"[thinking: {thinking[:200]}...{thinking[-200:]}]")
                else:
                    parts.append(f"[thinking: {thinking}]")
            elif btype == "tool_use":
                name = block.get("name", "unknown")
                inp = json.dumps(block.get("input", {}))
                if len(inp) > 200:
                    inp = inp[:200] + "..."
                parts.append(f"[tool_use: {name}({inp})]")
            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = json.dumps(result_content)
                length = len(str(result_content))
                err = " ERROR" if block.get("is_error") else ""
                parts.append(f"[tool_result:{err} {length} chars]")

        text = "\n".join(p for p in parts if p)
        if text:
            lines.append(f"--- Turn {i} ({role}) ---\n{text}\n")

    return "\n".join(lines)


def slugify(text, max_length=60):
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text[:max_length]


def session_date(events):
    for ev in events:
        ts = ev.get("timestamp", "")
        if ts:
            return ts[:10]
    return datetime.now().strftime("%Y-%m-%d")


def get_author():
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5,
        )
        name = result.stdout.strip()
        if name:
            return name
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return os.getenv("USER") or os.getenv("USERNAME") or "unknown"


def extract_slug(events):
    for ev in events[:30]:
        slug = ev.get("slug")
        if slug:
            return slug
    return None


def source_info(session_path, events=None):
    info = {
        "session_id": session_path.stem,
        "original_path": str(session_path),
        "project": None,
        "slug": None,
    }
    try:
        parts = session_path.resolve().parts
        proj_idx = parts.index("projects")
        info["project"] = parts[proj_idx + 1]
    except (ValueError, IndexError):
        pass
    if events:
        info["slug"] = extract_slug(events)
    return info


def resolve_slug(slug):
    """Find a session file whose early events contain the given slug."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return None
    for proj_dir in sorted(CLAUDE_PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        for session_file in sorted(proj_dir.glob("*.jsonl"),
                                    key=lambda p: p.stat().st_mtime, reverse=True):
            if session_file.stem.endswith(".usage"):
                continue
            try:
                with open(session_file) as f:
                    for i, line in enumerate(f):
                        if i > 30:
                            break
                        try:
                            obj = json.loads(line)
                            if obj.get("slug") == slug:
                                return session_file
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue
    return None


def write_annotation(session_path, annotation_data, output_dir=DEFAULT_OUTPUT_DIR,
                     model="claude-code-subagent", start=None, end=None):
    """Write an annotated copy of the session to output_dir (flat layout)."""
    session_path = Path(session_path).resolve()
    raw_lines, events = parse_jsonl(session_path)
    if not raw_lines:
        return None

    source = source_info(session_path, events)
    start_idx = start if start is not None else 0
    end_idx = end

    annotation = {
        "type": "annotation",
        "version": 1,
        "title": annotation_data.get("title", "Untitled session"),
        "author": get_author(),
        "tags": annotation_data.get("tags", []),
        "range": {
            "start_index": start_idx,
            "end_index": end_idx,
        },
        "sections": annotation_data.get("sections", []),
        "source": source,
        "generated": {
            "model": model,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "edited": False,
        },
    }

    date = session_date(events)
    title_slug = slugify(annotation["title"])
    filename = f"{date}-{title_slug}.jsonl"

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / filename
    counter = 1
    while out_path.exists():
        out_path = out_dir / f"{date}-{title_slug}-{counter}.jsonl"
        counter += 1

    with open(out_path, "w") as dst:
        for line in raw_lines:
            dst.write(line)
        dst.write(json.dumps(annotation) + "\n")

    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Helper for the session-annotator subagent",
    )
    parser.add_argument("path", nargs="?",
                        help="Path to a session JSONL file")
    parser.add_argument("--out", "-o", default=str(DEFAULT_OUTPUT_DIR),
                        metavar="DIR",
                        help=f"Output directory for --write-annotation "
                             f"(default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--start", type=int, default=None,
                        help="Start turn index for partial export")
    parser.add_argument("--end", type=int, default=None,
                        help="End turn index for partial export")
    parser.add_argument("--resolve", metavar="SLUG",
                        help="Resolve a what-three-words slug to a session path")
    parser.add_argument("--compress-only", action="store_true",
                        help="Print compressed session as JSON for subagent analysis")
    parser.add_argument("--write-annotation", action="store_true",
                        help="Read annotation JSON from stdin and write an annotated copy")

    args = parser.parse_args()

    if args.resolve:
        path = resolve_slug(args.resolve)
        if path:
            print(str(path))
        else:
            print(f"Error: slug '{args.resolve}' not found", file=sys.stderr)
            sys.exit(1)
        return

    if args.compress_only:
        if not args.path:
            parser.error("--compress-only requires a session path")
        path = Path(args.path).resolve()
        _, events = parse_jsonl(path)
        turns = merge_events(events)
        start = args.start if args.start is not None else 0
        end = args.end if args.end is not None else len(turns)
        compressed = compress_for_prompt(turns, start, end)

        # Write compressed text to a deterministic path under /tmp so the
        # caller (typically a Claude Code subagent) can Read it with
        # line-based offset/limit — sending it inline via stdout bloats
        # the Bash tool-results buffer on any non-trivial session.
        # /tmp rather than tempfile.gettempdir() so the path is stable
        # across users and matches a single settings.json allow rule.
        COMPRESSED_TMP_DIR.mkdir(parents=True, exist_ok=True)
        compressed_path = COMPRESSED_TMP_DIR / f"{path.stem}.compressed.txt"
        compressed_path.write_text(compressed)

        print(json.dumps({
            "turn_count": len(turns),
            "compressed_path": str(compressed_path),
            "compressed_bytes": len(compressed),
            "source": source_info(path, events),
            "date": session_date(events),
        }))
        return

    if args.write_annotation:
        if not args.path:
            parser.error("--write-annotation requires a session path")
        annotation_data = json.load(sys.stdin)
        out_path = write_annotation(
            Path(args.path), annotation_data, args.out,
            model="claude-code-subagent", start=args.start, end=args.end,
        )
        if out_path:
            print(str(out_path))
        else:
            print("Error: failed to write annotation", file=sys.stderr)
            sys.exit(1)
        return

    parser.error("Specify one of --resolve, --compress-only, or --write-annotation")


if __name__ == "__main__":
    main()
