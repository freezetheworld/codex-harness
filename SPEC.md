# Codex Harness — Spec

## What
A Python CLI tool that wraps `codex exec` with orchestration: chunking, progress, retry, structured output.

## Commands

```
codex-harness run "your prompt" [--chunk-size 10] [--model gpt-5.4-mini] [--max-retries 2]
```

## Features

### 1. Chunking
When a task has many files (>chunk-size), split into parallel batches. Use `files` hint in prompt like "Read these files: X, Y, Z". Recombine results.

### 2. Progress
Stream per-chunk status:
```
[1/4] auditing auth files... ✓
[2/4] auditing dashboard... (running)
[3/4] pending
[4/4] pending
```

### 3. Structured Output
Require Codex to write JSON to `_harness_result.json` per chunk:
```json
{"chunk": 1, "status": "success", "files_changed": [], "files_read": ["auth.tsx"], "output_summary": "Found 3 issues"}
```

### 4. Retry
If chunk fails (timeout, exit != 0), retry with smaller chunk or same chunk up to --max-retries.

### 5. Merge
After all chunks complete, write `HARNESS_REPORT.md` with combined findings.

## Architecture
- Single file: `codex_harness.py`
- Dependencies: subprocess, json, argparse, pathlib (no pip installs)
- Works from any project directory
- Test: `codex-harness run "Create file test_audit.txt with word PASS" --chunk-size 1`

## Rules
- NO code changes to the user's project — only read/write in $PWD
- Use `codex exec --full-auto` with YOLO/yes flags
- Kill runaway processes after 10 min per chunk
- Clean up _harness_temp/ after run
