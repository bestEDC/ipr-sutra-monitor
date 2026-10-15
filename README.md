# InvestPR Legislative Monitor (SUTRA)

A daily email alert of new Puerto Rico bills and resolutions on [SUTRA](https://sutra.oslpr.org) that match Invest Puerto Rico's keywords, plus movement on measures already sent.

## How it sends

```
01:47 UTC  GitHub Actions  .github/workflows/daily-alert.yml
             ├─ restore state/state.json from the `state` branch
             ├─ python -m sutra.daily   scan new IDs → keyword match → movement → Claude summaries → render
             ├─ scripts/publish_feed.sh  out/sutra.xml → bestEDC/ipr-sutra-feed (GitHub Pages)
             └─ scripts/state_save.sh   state back to the `state` branch
05:45 ET   HubSpot RSS email fetches https://bestedc.github.io/ipr-sutra-feed/sutra.xml
           and mails the item if its <guid> is new
```

There's no match and no movement → no new guid → no email.

## What counts as a match

Bills and resolutions only (proyectos, resoluciones). A measure is included when:
- **any keyword is in its official title:** desarrollo económico, inversión, Invest Puerto Rico, Ley 60, código de incentivos, incentivos, DDEC, Departamento de Desarrollo Económico y Comercio, Department of Economic Development and Commerce; or
- **a strong keyword is in the bill text:** DDEC, the Department's full name (Spanish or English), Invest Puerto Rico, Ley 60, código de incentivos.

Matching ignores accents and capitalization. Keywords live in `sutra_scan.py` (`KEYWORDS`) and `sutra/daily.py` (`STRONG_TEXT_KW`).

## Files

| Path | What |
|---|---|
| `sutra_scan.py` | SUTRA page parser, keyword patterns, document text extraction; also a CLI for month scans |
| `sutra/daily.py` | the daily run (exit 0 = alert built, 3 = nothing to send, 5 = past cutoff, 1 = failure) |
| `sutra/email_template.py` | the approved email design (BrandMaster Option A); don't restyle here |
| `sutra/claude_cli.py` | `claude -p` wrapper (subscription token, no API key) |
| `build_report.py` | monthly Excel report from a scan file |

## Secrets and variables

- `CLAUDE_CODE_OAUTH_TOKEN`: one-year token from `claude setup-token`. Never set `ANTHROPIC_API_KEY`.
- `FEED_DEPLOY_KEY`: SSH deploy key with write access to `bestEDC/ipr-sutra-feed`.
- Variable `CLAUDE_TOKEN_ISSUED` (YYYY-MM-DD): drives the token-age warning.

## Running by hand

```bash
python -m sutra.daily --state state/state.json --out out --dry-run                    # build, don't save
python -m sutra.daily --state s.json --out out --now 2026-10-14T02:00:00Z --dry-run   # replay a past morning
python -m sutra.daily --init --first-day 2026-10-15 --state state/state.json          # fresh state
```

Failures open a GitHub issue. A run summary is on each Actions run page.
