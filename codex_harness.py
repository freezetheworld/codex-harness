#!/usr/bin/env python3
"""
Codex Harness — thin orchestrator around `codex exec`.
Chunks large tasks, tracks progress, retries on failure, merges results.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── ANSI ─────────────────────────────────────────────────────
BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"

def ok(s):    return f"{GREEN}{s}{RESET}"
def fail(s):  return f"{RED}{s}{RESET}"
def dim(s):   return f"{DIM}{s}{RESET}"
def hl(s):    return f"{BOLD}{s}{RESET}"

# ── Run one codex chunk ──────────────────────────────────────
def run_chunk(chunk_id, prompt, model, workdir, timeout=600):
    """Run codex exec. Return {chunk, status, summary, stderr_tail}."""
    # Write prompt to temp file to avoid shell escaping issues
    prompt_file = Path(workdir) / f"_harness_prompt_{chunk_id}.txt"
    prompt_file.write_text(prompt)

    try:
        proc = subprocess.run(
            ["codex", "exec", "--full-auto", "--model", model, f"@{prompt_file}"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Extract "tokens used" line for summary
        tokens_line = ""
        for line in stdout.split("\n"):
            if "tokens used" in line.lower():
                tokens_line = line.strip()
                break

        # Last meaningful output line
        stdout_lines = [l for l in stdout.split("\n") if l.strip() and not l.startswith("diff --git")]
        summary = stdout_lines[-1][:200] if stdout_lines else "(no output)"

        return {
            "chunk": chunk_id,
            "status": "success" if proc.returncode == 0 else "failed",
            "summary": summary,
            "tokens": tokens_line,
            "stderr_tail": stderr[-200:] if stderr else "",
        }

    except subprocess.TimeoutExpired:
        return {"chunk": chunk_id, "status": "timeout", "summary": f"Timeout after {timeout}s", "tokens": "", "stderr_tail": ""}
    except Exception as e:
        return {"chunk": chunk_id, "status": "failed", "summary": str(e)[:200], "tokens": "", "stderr_tail": ""}
    finally:
        if prompt_file.exists():
            prompt_file.unlink()


# ── Run all chunks ──────────────────────────────────────────
def run_all(prompt, model, chunk_size, max_retries, workdir):
    workdir = Path(workdir).resolve()

    if "Read these files" in prompt:
        chunks = chunk_by_files(prompt, chunk_size)
    else:
        chunks = [prompt]

    total = len(chunks)
    results = []
    t0 = time.time()

    print(f"\n{hl('Codex Harness')}  {dim(str(total))} chunks  {CYAN}{model}{RESET}")
    print(f"  {dim(str(workdir))}\n")

    for i, chunk_prompt in enumerate(chunks, 1):
        label = chunk_prompt[:80].replace("\n", " ")
        if len(chunk_prompt) > 80:
            label += "..."

        print(f"  [{i}/{total}] {YELLOW}⏳{RESET} {label}", flush=True)

        result = None
        for attempt in range(1, max_retries + 1):
            result = run_chunk(i, chunk_prompt, model, str(workdir))
            if result["status"] == "success":
                break
            if attempt < max_retries:
                print(f"         {YELLOW}retry {attempt + 1}/{max_retries}{RESET}", flush=True)
                time.sleep(2)

        # Overwrite line with final status
        icon = ok("✓") if result["status"] == "success" else fail("✗")
        elapsed = time.time() - t0
        print(f"\033[1A\033[2K  [{i}/{total}] {icon} {label}  {dim(result.get('tokens', ''))}", flush=True)
        results.append(result)

    # Summary
    passed = sum(1 for r in results if r["status"] == "success")
    failed = total - passed

    print(f"\n{hl('Results:')} {ok(str(passed))} passed, {fail(str(failed))} failed  {dim(f'in {time.time()-t0:.0f}s')}")

    # Detailed output per chunk
    if failed:
        print(f"\n{hl('Details:')}")
        for r in results:
            if r["status"] != "success":
                print(f"  {fail('✗')} Chunk {r['chunk']}: {r['summary']}")
                if r.get("stderr_tail"):
                    print(f"    {dim('stderr:')} {r['stderr_tail'][:150]}")

    # Cleanup
    for f in workdir.glob("_harness_prompt_*.txt"):
        f.unlink()
    tmp = workdir / "_harness_temp_"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)

    return 0 if failed == 0 else 1


def chunk_by_files(prompt, chunk_size):
    """Split prompt by file list. Each chunk gets a subset of files."""
    files = re.findall(r'([\w/\-\.]+\.[\w]+)', prompt)
    files = [f for f in files if '.' in f and len(f) > 3 and '/' in f]

    if not files:
        return [prompt]

    # Extract non-file parts (prefix + suffix)
    prefix = prompt.split(files[0])[0].strip()
    suffix = ""
    last = files[-1]
    if last in prompt:
        suffix = prompt.split(last)[-1].strip()

    chunks = []
    for i in range(0, len(files), chunk_size):
        batch = files[i:i + chunk_size]
        file_list = ", ".join(batch)
        chunks.append(f"{prefix} {file_list} {suffix}".strip())

    return chunks


# ── CLI ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Codex Harness")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run")
    run.add_argument("prompt")
    run.add_argument("--chunk-size", type=int, default=10)
    run.add_argument("--model", default="gpt-5.4-mini")
    run.add_argument("--max-retries", type=int, default=2)
    run.add_argument("--workdir", default=".")

    args = parser.parse_args()

    if args.command == "run":
        sys.exit(run_all(args.prompt, args.model, args.chunk_size, args.max_retries, args.workdir))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
