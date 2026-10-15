#!/usr/bin/env bash
# Push out/sutra.xml to this repo's `gh-pages` branch, served at
# https://bestedc.github.io/ipr-sutra-monitor/sutra.xml, which HubSpot's RSS email reads.
# A new <guid> in the feed IS the send, so this only runs when sutra.daily exited 0.
# Uses the workflow's own GITHUB_TOKEN (contents: write); no deploy key needed.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -s out/sutra.xml ] || { echo "publish: out/sutra.xml missing" >&2; exit 1; }
site="$(mktemp -d)"
trap 'rm -rf "$site"' EXIT
git fetch -q origin gh-pages
git worktree add -q "$site" origin/gh-pages
cp out/sutra.xml "$site/sutra.xml"
if git -C "$site" diff --quiet -- sutra.xml 2>/dev/null && git -C "$site" ls-files --error-unmatch sutra.xml >/dev/null 2>&1; then
    echo "feed unchanged"
else
    git -C "$site" add sutra.xml
    git -C "$site" -c user.name="investpr-sutra-monitor" -c user.email="sutra-monitor@investpr.invalid" \
        commit -qm "Alert $(date -u +%F)"
    git -C "$site" push -q origin HEAD:gh-pages
    echo "published https://bestedc.github.io/ipr-sutra-monitor/sutra.xml"
fi
git worktree remove --force "$site"
