#!/usr/bin/env bash
# Save state/state.json to the `state` branch as a single orphan commit (no history growth).
# Refuses to save a state that looks truncated: a lost state would re-mail old measures.
set -euo pipefail
cd "$(dirname "$0")/.."
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-investpr-sutra-monitor}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-sutra-monitor@investpr.invalid}"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

python - <<'PY'
import json, subprocess, sys
new = json.load(open("state/state.json"))
if not new.get("seen") or not new.get("last_max_id"):
    sys.exit("state: REFUSING to save an empty state")
try:
    old = json.loads(subprocess.run(["git", "show", "origin/state:state.json"],
                                    capture_output=True, text=True, check=True).stdout)
except subprocess.CalledProcessError:
    old = None
if old:
    if new["last_max_id"] < old["last_max_id"]:
        sys.exit(f"state: REFUSING, last_max_id went backwards {old['last_max_id']} -> {new['last_max_id']}")
    lost = set(old["tracked"]) - set(new["tracked"])
    if lost:
        sys.exit(f"state: REFUSING, {len(lost)} tracked measures would be lost")
PY

blob=$(git hash-object -w state/state.json)
tree=$(printf '100644 blob %s\tstate.json\n' "$blob" | git mktree)
commit=$(git commit-tree "$tree" -m "state $(date -u +%FT%TZ)")
git push -q -f origin "$commit:refs/heads/state"
echo "state saved ($commit)"
