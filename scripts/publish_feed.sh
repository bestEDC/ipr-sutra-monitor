#!/usr/bin/env bash
# Push out/sutra.xml to the public Pages repo that HubSpot's RSS email reads.
# A new <guid> in the feed IS the send, so this only runs when sutra.daily exited 0.
#   FEED_REPO_URL  git remote of the Pages repo (SSH, with a deploy key in CI)
#   FEED_REPO_DIR  working clone location
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${FEED_REPO_DIR:-$PWD/.feedrepo}"
FEED_REPO_URL="${FEED_REPO_URL:-git@github.com:bestEDC/ipr-sutra-feed.git}"
[ -s out/sutra.xml ] || { echo "publish: out/sutra.xml missing" >&2; exit 1; }
[ -d "$REPO/.git" ] || git clone -q --depth 1 "$FEED_REPO_URL" "$REPO"
git -C "$REPO" pull -q --rebase origin main
cp out/sutra.xml "$REPO/sutra.xml"
if git -C "$REPO" diff --quiet -- sutra.xml; then
    echo "feed unchanged"
    exit 0
fi
git -C "$REPO" add sutra.xml
git -C "$REPO" -c user.name="investpr-sutra-monitor" -c user.email="sutra-monitor@investpr.invalid" \
    commit -qm "Alert $(date -u +%F)"
git -C "$REPO" push -q origin HEAD:main
echo "published https://bestedc.github.io/ipr-sutra-feed/sutra.xml"
