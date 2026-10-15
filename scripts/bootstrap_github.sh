#!/usr/bin/env bash
# One-time GitHub setup for the SUTRA monitor. Run by Ian from the project folder:
#   ! scripts/bootstrap_github.sh
# Creates the two PUBLIC repos, pushes the code and the feed site, enables Pages,
# installs the feed deploy key, and uploads the starting state. Safe to read before running.
set -euo pipefail
cd "$(dirname "$0")/.."
OWNER=bestEDC

echo "1/6 code repo"
gh repo create "$OWNER/ipr-sutra-monitor" --public \
  --description "InvestPR Legislative Monitor: daily SUTRA keyword alert" --source . --push

echo "2/6 feed repo"
gh repo create "$OWNER/ipr-sutra-feed" --public \
  --description "Public RSS feed for the InvestPR Legislative Monitor (read by HubSpot)"
( cd .feed_site
  git init -q -b main
  git add -A
  git -c user.name="$(git -C .. config user.name)" -c user.email="$(git -C .. config user.email)" commit -qm "Feed site"
  git remote add origin "https://github.com/$OWNER/ipr-sutra-feed.git"
  git push -q -u origin main )

echo "3/6 GitHub Pages"
gh api -X POST "repos/$OWNER/ipr-sutra-feed/pages" -f "source[branch]=main" -f "source[path]=/" >/dev/null

echo "4/6 deploy key (write access to the feed repo only)"
gh repo deploy-key add .keys/feed_deploy_key.pub --repo "$OWNER/ipr-sutra-feed" --allow-write --title "ipr-sutra-monitor"
gh secret set FEED_DEPLOY_KEY --repo "$OWNER/ipr-sutra-monitor" < .keys/feed_deploy_key
rm -f .keys/feed_deploy_key   # the private key now lives only in the repo secret

echo "5/6 starting state"
scripts/state_save.sh

echo "6/6 done. Next: Claude token (see instructions), then run the probe:"
echo "  gh workflow run probe.yml --repo $OWNER/ipr-sutra-monitor"
