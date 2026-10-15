#!/usr/bin/env bash
# Restore state/state.json from the repo's `state` branch (created by state_save.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p state
git fetch -q origin state
git show origin/state:state.json > state/state.json
python - <<'PY'
import json
s = json.load(open("state/state.json"))
print(f"state restored: last_max_id={s['last_max_id']}, seen={len(s['seen'])}, tracked={len(s['tracked'])}, pending={len(s['pending'])}")
PY
