"""Locating and calling the Claude Code CLI (`claude -p`). Copied from ipr-media-monitoring src/claude_cli.py.

SUTRA summaries shell out to the CLI under Ian's subscription.
NO API key, NO anthropic SDK.

WHY THIS MODULE EXISTS. Both callers used a bare `"claude"` argv[0] and caught
OSError to mean "the model declined". Those are not the same failure. In any
environment that does not source a login shell — launchd, cron, a CI runner —
PATH is `/usr/bin:/bin:/usr/sbin:/sbin`, while the CLI installs to
`~/.local/bin/claude`. So exec raised FileNotFoundError, the handler swallowed
it, and every article came back "errored": the run reported success, wrote no
analyses, and the digest went out empty. That is precisely what happened to
every unattended run from 2026-07-30 to 2026-08-07 — `{'analyzed': 0,
'errors': 60}` eight days running, while the same code invoked by hand from a
terminal returned `{'analyzed': 59, 'errors': 1}`.

Two rules follow, and they are the whole point of the file:

  * Resolve the binary explicitly, not through whatever PATH we inherit.
  * Never let "the CLI is unreachable" masquerade as "the model said no".
    `missing()` and `preflight()` let run_daily refuse to start instead of
    spending an hour producing nothing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Checked in order, after $CLAUDE_BIN and a PATH lookup. These are the install
# locations the CLI has used across versions; a box that has any of them works
# unattended without the caller having to fix up PATH first.
_CANDIDATES = (
    Path.home() / ".local" / "bin" / "claude",
    Path.home() / ".claude" / "local" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
)


@lru_cache(maxsize=1)
def binary() -> Optional[str]:
    """Absolute path to the Claude Code CLI, or None if it is not installed."""
    override = os.environ.get("CLAUDE_BIN")
    if override and os.access(override, os.X_OK):
        return override
    found = shutil.which("claude")
    if found:
        return found
    for path in _CANDIDATES:
        if os.access(path, os.X_OK):
            return str(path)
    return None


def missing() -> bool:
    return binary() is None


# Why the most recent run_json call returned None — "timeout after 900s",
# "exit 1 — ...", "unparseable reply". A None from this module collapses
# timeout, refusal, and garbage into one value; the 2026-08-19 lost issue
# (three 300s timeouts read as "the LLM flaked") took an artifact download
# and second-counting to diagnose because nothing recorded which it was.
last_failure: str = ""

# When the CLI refused because the subscription's usage limit is exhausted, the epoch second at
# which it lifts (the CLI appends "|<epoch>" to that message), else None. Callers can wait for it.
last_retry_after: Optional[int] = None

_LIMIT_RE = re.compile(r"usage limit|rate limit|hit your limit|limit reached", re.I)
_EPOCH_RE = re.compile(r"\|(\d{10})\b")


def _failure_message(stdout: str, stderr: str) -> str:
    """The CLI's own explanation of a failed call. On exit 1 the CLI still prints its JSON
    result object, and the reason ("Failed to authenticate…", "usage limit reached|…") is in
    its `result` field, 400+ bytes in. Dumping the first 300 bytes of that object, as the
    2026-10-16/17 runs did, records the session id and zero token counts and loses the reason."""
    global last_retry_after
    last_retry_after = None
    msg = ""
    try:
        payload = json.loads((stdout or "").strip())
        if isinstance(payload, dict):
            msg = str(payload.get("result") or "").strip()
    except json.JSONDecodeError:
        pass
    if not msg:
        msg = ((stderr or "").strip() or (stdout or "").strip())[:300] or "no output"
    if _LIMIT_RE.search(msg):
        m = _EPOCH_RE.search(msg)
        if m:
            last_retry_after = int(m.group(1))
    return msg[:400]


def run_json(prompt: str, timeout: int) -> Optional[dict]:
    """Run a prompt and return the JSON object the model emitted, or None.

    None means the call failed OR the reply was unparseable — callers already
    treat both as "no verdict" (`last_failure` says which). What must never
    reach here is a missing binary: check `missing()` at startup so that
    failure surfaces as itself.
    """
    global last_failure
    exe = binary()
    if exe is None:
        last_failure = "claude CLI not found"
        return None
    try:
        # The prompt goes on stdin, never in argv: Linux caps one argv element
        # at 128 KiB (MAX_ARG_STRLEN), and the 2026-08-21 merge prompt crossed
        # it — every attempt died with E2BIG before reaching the model, in CI
        # only (macOS has no per-argument cap, so local replays passed).
        proc = subprocess.run(
            [exe, "-p", "--output-format", "json"],
            input=prompt,
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            last_failure = f"exit {proc.returncode} — {_failure_message(proc.stdout, proc.stderr)}"
            return None
        payload = json.loads(proc.stdout)
        if payload.get("is_error"):
            last_failure = f"error — {_failure_message(proc.stdout, proc.stderr)}"
            return None
        raw = payload.get("result", "")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            last_failure = f"no JSON object in reply — {raw.strip()[:200]!r}"
            return None
        last_failure = ""
        return json.loads(match.group(0))
    except subprocess.TimeoutExpired:
        last_failure = f"timeout after {timeout}s"
        return None
    except json.JSONDecodeError as exc:
        last_failure = f"unparseable reply — {exc}"
        return None
    except (subprocess.SubprocessError, OSError) as exc:
        last_failure = f"{type(exc).__name__}: {exc}"
        return None


def preflight(timeout: int = 120) -> tuple[bool, str]:
    """Prove the CLI is installed AND authenticated before the pipeline commits
    to a long run. Returns (ok, detail)."""
    exe = binary()
    if exe is None:
        return False, ("claude CLI not found — looked at $CLAUDE_BIN, PATH, and "
                       + ", ".join(str(p) for p in _CANDIDATES))
    try:
        # Same invocation shape as run_json (prompt on stdin) — a preflight
        # that execs differently from the real calls can pass while they fail.
        proc = subprocess.run(
            [exe, "-p", "--output-format", "json"],
            input="Reply with exactly: OK",
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.SubprocessError as exc:
        return False, f"{exe}: {type(exc).__name__}"
    except OSError as exc:
        return False, f"{exe}: {exc}"
    if proc.returncode != 0:
        # Report BOTH streams. The CLI puts its refusal on stdout as often as
        # stderr, and a preflight whose failure message is "exit 1 — " tells
        # the person reading the run log nothing at all.
        detail = " | ".join(part for part in (
            f"stderr: {(proc.stderr or '').strip()[:400]}" if (proc.stderr or '').strip() else "",
            f"stdout: {(proc.stdout or '').strip()[:400]}" if (proc.stdout or '').strip() else "",
        ) if part) or "no output on either stream"
        return False, f"{exe}: exit {proc.returncode} — {detail}"
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, f"{exe}: unparseable output — {proc.stdout[:200]}"
    if payload.get("is_error"):
        return False, f"{exe}: {str(payload.get('result'))[:300]}"
    return True, f"{exe} ok"
