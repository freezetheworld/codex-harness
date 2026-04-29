#!/usr/bin/env python3
"""
Codex Harness — thin orchestrator around `codex exec`.
Chunks large tasks, tracks progress, retries on failure, merges results.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── ANSI colors ──────────────────────────────────────────────
C = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
}


def c(tag, text):
    return f"{C.get(tag, '')}{text}{C['reset']}"


def status(chunk, total, label, state="running"):
    symbols = {"running": "⏳", "done": "✓", "failed": "✗", "pending": "○"}
    colors = {"running": "yellow", "done": "green", "failed": "red", "pending": "dim"}
    sym = symbols.get(state, "?")
    col = colors.get(state, "dim")
    return f"  [{chunk}/{total}] {c(col, sym)} {label}"


# ── Run one chunk ───────────────────────────────────────────
def run_chunk(chunk_id, prompt, model, workdir, timeout=600):
    """Run codex exec for a single chunk. Returns dict with result."""
    result_file = Path(workdir) / f"_harness_result_{chunk_id}.json"
    full_prompt = f"""{prompt}

IMPORTANT: When you finish, write a JSON summary to {result_file}:
{{"chunk": {chunk_id}, "status": "success"|"failed", "files_changed": [...], "files_read": [...], "output_summary": "..."}}"""

    try:
        proc = subprocess.run(
            ["codex", "exec", "--full-auto", "--model", model, full_prompt],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result_file.exists():
            try:
                data = json.loads(result_file.read_text())
                return data
            except json.JSONDecodeError:
                pass

        # Fallback: parse from stdout
        return {
            "chunk": chunk_id,
            "status": "success" if proc.returncode == 0 else "failed",
            "files_changed": [],
            "files_read": [],
            "output_summary": proc.stdout[-500:] if proc.stdout else "(no output)",
        }

    except subprocess.TimeoutExpired:
        return {"chunk": chunk_id, "status": "timeout", "files_changed": [], "files_read": [], "output_summary": "Timeout after {timeout}s"}
    except Exception as e:
        return {"chunk": chunk_id, "status": "failed", "files_changed": [], "files_read": [], "output_summary": str(e)}


# ── Run all chunks ──────────────────────────────────────────
def run_all(prompt, model, chunk_size, max_retries, workdir):
    workdir = Path(workdir).resolve()
    tmpdir = Path(tempfile.mkdtemp(prefix="_harness_temp_", dir=workdir))

    chunks = generate_chunks(prompt, chunk_size)
    total = len(chunks)
    results = []

    print(f"\n{c('bold', 'Codex Harness')} — {total} chunk(s) — {c('cyan', model)}")
    print(f"  {c('dim', workdir)}\n")

    for i, chunk_prompt in enumerate(chunks, 1):
        chunk_id = i
        label = chunk_prompt[:80].replace("\n", " ") + ("..." if len(chunk_prompt) > 80 else "")
        print(status(i, total, label, "running"), end="\r", flush=True)

        result = None
        for attempt in range(1, max_retries + 1):
            result = run_chunk(chunk_id, chunk_prompt, model, str(workdir))
            if result.get("status") == "success":
                break
            if attempt < max_retries:
                print(status(i, total, f"{label} (retry {attempt + 1}/{max_retries})", "running"), end="\r", flush=True)
                time.sleep(2)

        state = "done" if result.get("status") == "success" else "failed"
        print(status(i, total, label, state))
        results.append(result)

    # Merge report
    print(f"\n{c('bold', 'Merging results...')}")
    write_report(results, workdir)

    # Cleanup
    for f in workdir.glob("_harness_result_*.json"):
        f.unlink()
    if tmpdir.exists():
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    failed = sum(1 for r in results if r.get("status") != "success")
    if failed:
        print(f"\n{c('red', f'✗ {failed} chunk(s) failed')}")
        sys.exit(1)
    else:
        print(f"\n{c('green', '✓ All chunks passed')}\n")


def generate_chunks(prompt, chunk_size):
    """Split prompt into chunks. Simple: if prompt mentions files, split by files."""
    if chunk_size <= 1 or "Read these files" not in prompt and "read every file" not in prompt.lower():
        return [prompt]

    # Extract file list from prompt
    import re
    files = re.findall(r'([\w/\-\.]+\.[\w]+)', prompt)
    files = [f for f in files if '.' in f and len(f) > 2]

    chunks = []
    for i in range(0, len(files), chunk_size):
        batch = files[i:i + chunk_size]
        chunks.append(f"Read these files: {', '.join(batch)}. Complete your assigned portion of the audit and write results.")

    return chunks if chunks else [prompt]


def write_report(results, workdir):
    """Write HARNESS_REPORT.md with combined findings."""
    lines = ["# Harness Report", "", f"**Model:** {results[0].get('_model', 'unknown')}", f"**Chunks:** {len(results)}", ""]

    success = sum(1 for r in results if r.get("status") == "success")
    failed = len(results) - success
    lines.append(f"**Status:** {success} passed, {failed} failed")
    lines.append("")

    for r in results:
        icon = "✓" if r.get("status") == "success" else "✗"
        lines.append(f"## Chunk {r['chunk']} {icon}")
        lines.append(f"- **Status:** {r.get('status')}")
        lines.append(f"- **Files changed:** {', '.join(r.get('files_changed', [])) or 'none'}")
        lines.append(f"- **Files read:** {', '.join(r.get('files_read', [])) or 'none'}")
        lines.append(f"- **Summary:** {r.get('output_summary', 'N/A')}")
        lines.append("")

    report_path = Path(workdir) / "HARNESS_REPORT.md"
    report_path.write_text("\n".join(lines))
    print(f"  {c('green', 'Report:')} {report_path}")


# ── CLI ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Codex Harness — orchestrate codex exec")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run a prompt through the harness")
    run.add_argument("prompt", help="The prompt to execute")
    run.add_argument("--chunk-size", type=int, default=10, help="Files per chunk (default: 10)")
    run.add_argument("--model", default="gpt-5.4-mini", help="Codex model (default: gpt-5.4-mini)")
    run.add_argument("--max-retries", type=int, default=2, help="Max retries per chunk (default: 2)")
    run.add_argument("--workdir", default=".", help="Working directory")

    args = parser.parse_args()

    if args.command == "run":
        run_all(args.prompt, args.model, args.chunk_size, args.max_retries, args.workdir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
