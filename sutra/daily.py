"""Daily SUTRA legislative alert: scan → match → movement → summarize → render → feed.

    python -m sutra.daily --state state/state.json --out out/            # normal run
    python -m sutra.daily --init --first-day 2026-10-15 --state state/state.json   # first-time setup, no email
    python -m sutra.daily ... --now 2026-10-14T02:00:00Z --dry-run       # replay a past day locally

Delivery is a HubSpot RSS email that fetches out/sutra.xml from GitHub Pages and mails any item
whose <guid> is new. So writing a feed with a new guid IS the send; writing nothing sends nothing.

Exit status (the workflow acts on it):
  0  feed written: there is an alert to mail
  3  nothing to send today (no new matches, no movement): not a fault
  5  past the publish cutoff: HubSpot already fetched today, state left untouched so tomorrow retries
  1  real failure
"""
import argparse
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import sutra_scan as scan  # noqa: E402
from sutra import claude_cli, email_template  # noqa: E402

# A keyword in the bill TEXT only counts when it is one of these; the generic ones
# ("desarrollo económico", "inversión", "incentivos") appear in passing in most bills.
STRONG_TEXT_KW = ["DDEC", "Departamento de Desarrollo Económico y Comercio",
                  "Department of Economic Development and Commerce", "Invest Puerto Rico",
                  "Ley 60", "código de incentivos"]

FEED_URL = os.environ.get("FEED_URL", "https://bestedc.github.io/ipr-sutra-feed/sutra.xml")
LOGO_URL = os.environ.get("LOGO_URL", "https://bestedc.github.io/ipr-sutra-feed/assets/investpr-logo-horizontal-white.png")
FEED_TITLE = "InvestPR Legislative Monitor"

# HubSpot's RSS email fetches at 05:45 US/Eastern = 09:45 UTC (EDT) / 10:45 UTC (EST).
# A feed written after this would never be mailed for today, so the run backs off instead.
PUBLISH_CUTOFF_UTC = (9, 30)
SEND_MOVEMENT_ONLY = True        # a day with movement but no new matches still sends
PENDING_DOC_DAYS = 14            # keep re-checking a measure whose document is not posted yet
SCAN_BACK = 60                   # re-read this many IDs below the last max (late-numbered filings)
EMPTY_STOP = 40                  # stop scanning upward after this many consecutive empty IDs
FINAL_EVENT = re.compile(r"firmad|vetad|retirad|convertid[ao] en ley|aprobada? por (el|la) goberna", re.I)

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]

# SUTRA event names → English. Anything not listed is shown as SUTRA writes it.
EVENTS_EN = [
    (r"^Radicad[oa]$", "filed"),
    (r"^Aparece en Primera Lectura de la Cámara$", "first reading in the House"),
    (r"^Aparece en Primera Lectura del Senado$", "first reading in the Senate"),
    (r"^Referid[oa] a Comisi[oó]n\(es\)$", "referred to committee"),
    (r"^Remitid[oa] a Comisi[oó]n de Calendarios de la Cámara$", "sent to the House Calendar Committee"),
    (r"^Remitid[oa] a Comisi[oó]n de Asuntos Internos del Senado$", "sent to the Senate Internal Affairs Committee"),
    (r"^En el Calendario de [OÓ]rdenes Especiales de la Cámara$", "placed on the House special orders calendar"),
    (r"^En el Calendario de [OÓ]rdenes Especiales del Senado$", "placed on the Senate special orders calendar"),
    (r"^Aprobad[oa] con enmiendas en sala$", "approved with floor amendments"),
    (r"^Aprobad[oa] por Cámara en Votaci[oó]n Final$", "approved by the House in final vote"),
    (r"^Aprobad[oa] por Senado en Votaci[oó]n Final$", "approved by the Senate in final vote"),
    (r"^Texto de Aprobaci[oó]n Final enviado al Senado$", "final approved text sent to the Senate"),
    (r"^Texto de Aprobaci[oó]n Final enviado a la Cámara$", "final approved text sent to the House"),
    (r"^Informe Positivo", "favorable committee report"),
    (r"^Informe Negativo", "unfavorable committee report"),
    (r"^Vista P[uú]blica", "public hearing"),
]


def log(msg):
    print(msg, flush=True)


def long_date(d):
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def short_date(mmddyyyy):
    d = datetime.strptime(mmddyyyy, "%m/%d/%Y").date()
    return f"{MONTHS[d.month - 1][:3]} {d.day}"


def event_en(name):
    for pat, en in EVENTS_EN:
        if re.search(pat, name.strip(), re.I):
            return en
    return name.strip()


def describe_events(events):
    parts = [f"{event_en(ev['evento'])} ({short_date(ev['fecha'])})" for ev in events]
    text = " → ".join(parts)
    return text[:1].upper() + text[1:] + "."


def doc_label(url, original):
    name = url.rsplit("/", 1)[-1].lower()
    kind = "PDF" if name.endswith(".pdf") else "Word"
    if original:
        return f"Read the bill ({kind})"
    if re.search(r"ta\.(docx?|pdf)$", name):
        return "Read the approved text"
    if re.search(r"-i\.(docx?|pdf)$|informe", name):
        return "Read the committee report"
    if "votacion" in name:
        return "See the vote"
    return f"Read the latest document ({kind})"


# ---------------------------------------------------------------- state

def load_state(path):
    with open(path) as f:
        return json.load(f)


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def init_state(path, start_id, first_day):
    """Mark everything filed before first_day as already seen, so the first email covers
    first_day's filings only, not a flood of old ones."""
    found = scan_new({"last_max_id": start_id + SCAN_BACK})
    seen = {str(m["id"]): m["fecha_radicacion"] for m in found if m["fecha_radicacion"] < first_day}
    if not seen:
        sys.exit("--init found no measures; check --start-id and that SUTRA is reachable")
    state = {"last_max_id": max(int(i) for i in seen), "seen": seen, "pending": {}, "tracked": {},
             "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    save_state(path, state)
    log(f"initialized {path}: last_max_id={state['last_max_id']}, {len(seen)} measures before {first_day} marked seen")


# ---------------------------------------------------------------- SUTRA access

def get_measure(mid):
    try:
        return scan.parse_measure(mid, scan.fetch(f"{scan.BASE}/medidas/{mid}"))
    except Exception as exc:  # network errors are retried by the next day's re-scan window
        log(f"  fetch error {mid}: {exc}")
        return "error"


def scan_new(state, workers=6):
    """Read IDs from last_max_id-SCAN_BACK upward until EMPTY_STOP consecutive empty pages."""
    start = max(state["last_max_id"] - SCAN_BACK, 1)
    found, empty, mid, errors = [], 0, start, 0
    with ThreadPoolExecutor(workers) as ex:
        while empty < EMPTY_STOP and errors <= 20:
            batch = list(range(mid, mid + workers * 2))
            mid = batch[-1] + 1
            for i, m in zip(batch, ex.map(get_measure, batch)):
                if m == "error":
                    errors += 1
                    empty += 1  # an unreachable site must end the scan, not spin forever
                    continue
                if m is None:
                    empty += 1
                    continue
                empty = 0
                found.append(m)
    if errors > 20:
        raise RuntimeError(f"SCAN FAILED: {errors} fetch errors; SUTRA unreachable from this runner?")
    log(f"scan: read IDs {start}..{mid - 1}, {len(found)} measures on SUTRA, {errors} fetch errors")
    return found


def text_keywords(m, cache_dir):
    """(status, strong keywords found in the document text)."""
    try:
        txt = scan.fetch_document_text(m, cache_dir)
    except Exception as exc:
        log(f"  document error {m['numero']}: {exc}")
        return "error", []
    if txt is None:
        return "no-doc", []
    nt = scan.norm(txt)
    return "ok", [kw for kw in STRONG_TEXT_KW if scan.PATTERNS[kw].search(nt)]


# ---------------------------------------------------------------- summaries

PROMPT = """You write short entries for a daily internal email that alerts Invest Puerto Rico
(InvestPR, Puerto Rico's private nonprofit investment-promotion organization) to new bills and
resolutions filed in the Puerto Rico Legislature that touch economic development, investment,
the incentives code (Ley 60-2019, Código de Incentivos), DDEC, or InvestPR.

For EACH measure below, write:
- "headline": one plain-English line, at most 14 words, saying what the measure does.
  No measure number, no "This bill".
- "why": at most 40 words, one or two factual English sentences on why the measure is relevant
  to economic development / investment / incentives work in Puerto Rico. Explain the relevance;
  don't restate the headline. Neutral and factual: no opinions, no ratings, no political
  commentary, no advice. If the keyword match is incidental (e.g. DDEC only listed as one member
  of an unrelated committee, or a road named for someone's "economic development" work), say so
  in one short sentence.

Base everything only on the official title, the text excerpt and the keyword passages given.
"keyword_passages" shows where each matched keyword appears in the bill text. Do not invent
figures or dates. Never mention "the excerpt" or what you were or weren't given.

Return ONLY a JSON object: {"items": [{"id": <id>, "headline": "...", "why": "..."}, ...]}

MEASURES:
"""


def excerpt(m, cache_dir, limit=6000):
    p = Path(cache_dir) / f"{m['numero']}.txt"
    if not p.exists():
        return ""
    t = re.sub(r"\s+", " ", p.read_text(errors="replace"))
    i = t.find("EXPOSICI")
    return t[i if i > 0 else 0:][:limit]


def keyword_passages(m, cache_dir, per_kw=2, width=300):
    p = Path(cache_dir) / f"{m['numero']}.txt"
    if not p.exists():
        return {}
    t = re.sub(r"\s+", " ", p.read_text(errors="replace"))
    nt = scan.norm(t)  # same length as t only if norm keeps characters 1:1, so search on t's lowercase
    out = {}
    for kw in m["kw_display"]:
        pat = scan.PATTERNS.get(kw)
        if not pat:
            continue
        spans = [mm.span() for mm in pat.finditer(nt)][:per_kw]
        if spans and len(nt) == len(t):
            out[kw] = [t[max(0, a - width):b + width] for a, b in spans]
    return out


def summarize(items, cache_dir):
    """Returns {id: {headline, why}}; missing ids fall back in the caller."""
    blocks = []
    for m in items:
        blocks.append(json.dumps({"id": m["id"], "number": m["numero"], "type": m["tipo"],
                                  "keywords": m["kw_display"], "official_title": m["titulo"],
                                  "text_excerpt": excerpt(m, cache_dir),
                                  "keyword_passages": keyword_passages(m, cache_dir)}, ensure_ascii=False))
    prompt = PROMPT + "\n".join(blocks)
    for attempt in range(3):
        out = claude_cli.run_json(prompt, timeout=300)
        if out and isinstance(out.get("items"), list):
            res = {}
            for it in out["items"]:
                try:
                    if it.get("headline") and it.get("why"):
                        res[int(it["id"])] = {"headline": it["headline"].strip(), "why": it["why"].strip()}
                except (TypeError, ValueError):
                    pass
            log(f"summaries: {len(res)}/{len(items)} from Claude")
            return res
        log(f"summaries: attempt {attempt + 1} failed: {claude_cli.last_failure}")
    log("SUMMARY DEGRADED: Claude unavailable, sending with fallback text")
    return {}


def fallback_summary(m):
    words = m["titulo"].split()
    return {"headline": " ".join(words[:14]) + (" …" if len(words) > 14 else ""),
            "why": "Summary unavailable today; see the official title below."}


# ---------------------------------------------------------------- feed

def build_feed(fragment, mail_date, built_at, title):
    rss = ET.Element("rss", {"version": "2.0", "xmlns:atom": "http://www.w3.org/2005/Atom",
                             "xmlns:content": "http://purl.org/rss/1.0/modules/content/"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = FEED_TITLE
    ET.SubElement(ch, "link").text = FEED_URL
    ET.SubElement(ch, "description").text = "New Puerto Rico bills and resolutions matching InvestPR keywords (SUTRA)."
    ET.SubElement(ch, "language").text = "en-us"
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(built_at)
    ET.SubElement(ch, "atom:link", {"href": FEED_URL, "rel": "self", "type": "application/rss+xml"})
    item = ET.SubElement(ch, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = FEED_URL
    # One alert per mail date: a same-day rerun replaces the item instead of mailing twice.
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"{FEED_URL}#alert-{mail_date.isoformat()}"
    ET.SubElement(item, "pubDate").text = format_datetime(built_at)
    # Same HTML under both names: HubSpot's {{ post.summary }} may resolve to either.
    ET.SubElement(item, "description").text = fragment
    ET.SubElement(item, "content:encoded").text = fragment
    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


# ---------------------------------------------------------------- main run

def run(args):
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    mail_date = now.date()                      # the morning HubSpot mails this
    filed_day = mail_date - timedelta(days=1)   # "measures filed yesterday"
    if not args.ignore_cutoff and (now.hour, now.minute) >= PUBLISH_CUTOFF_UTC:
        log(f"TOO LATE: {now:%H:%M} UTC is past the {PUBLISH_CUTOFF_UTC[0]:02d}:{PUBLISH_CUTOFF_UTC[1]:02d} cutoff; "
            "state untouched, tomorrow's run picks everything up")
        return 5

    state = load_state(args.state)
    cache = args.cache or tempfile.mkdtemp(prefix="sutra-docs-")
    os.makedirs(cache, exist_ok=True)
    today = mail_date.isoformat()

    # 1. New measures on SUTRA since the last run
    # SUTRA interleaves digitized historical records (e.g. 1990) in the ID sequence: skip them.
    # On a replay (--now), also ignore anything filed after the replayed day.
    oldest = (mail_date - timedelta(days=365)).isoformat()
    newest = filed_day.isoformat() if args.now else "9999-12-31"
    fresh = [m for m in scan_new(state) if str(m["id"]) not in state["seen"]
             and oldest <= m["fecha_radicacion"] <= newest]
    for m in fresh:
        state["seen"][str(m["id"])] = m["fecha_radicacion"]
        state["last_max_id"] = max(state["last_max_id"], m["id"])
    candidates = [m for m in fresh if scan.TYPE_RE.match(m["tipo"])]
    log(f"new on SUTRA: {len(fresh)} measures, {len(candidates)} bills/resolutions")

    # 2. Measures whose document was missing earlier: re-check
    for mid, first in list(state["pending"].items()):
        if mid in state["tracked"] or (mail_date - date.fromisoformat(first)).days > PENDING_DOC_DAYS:
            del state["pending"][mid]
            continue
        m = get_measure(int(mid))
        if isinstance(m, dict) and m["documentos"]:
            candidates.append(m)

    # 3. Keyword matching: any keyword in the title, or a strong keyword in the bill text
    matches = []
    for m in candidates:
        title_kw = scan.match_keywords(m["titulo"])
        status, text_kw = text_keywords(m, cache)
        if status == "no-doc":
            state["pending"].setdefault(str(m["id"]), today)
        else:
            state["pending"].pop(str(m["id"]), None)
        if (title_kw or text_kw) and str(m["id"]) not in state["tracked"]:
            m["kw_display"] = list(dict.fromkeys(title_kw + text_kw))
            matches.append(m)
    matches.sort(key=lambda m: (m["fecha_radicacion"], m["numero"]))
    log(f"matches: {len(matches)} " + " ".join(m["numero"] for m in matches))

    # 4. Movement on measures already sent
    movement = []
    live = [mid for mid, t in state["tracked"].items() if not t.get("final")]
    with ThreadPoolExecutor(6) as ex:
        pages = dict(zip(live, ex.map(lambda i: get_measure(int(i)), live)))
    for mid, m in pages.items():
        if not isinstance(m, dict):
            continue
        t = state["tracked"][mid]
        known = {(ev["evento"], ev["fecha"]) for ev in t["events"]}
        new_ev = [ev for ev in m["eventos"] if (ev["evento"], ev["fecha"]) not in known]
        if not new_ev:
            continue
        new_docs = [d for d in m["documentos"] if d not in t.get("docs", [])]
        movement.append({"mid": mid, "m": m, "t": t, "events": new_ev, "new_docs": new_docs})
    log(f"movement: {len(movement)} tracked measures with new events (of {len(live)} tracked)")

    if not matches and not (movement and SEND_MOVEMENT_ONLY):
        log("nothing to send today")
        if not args.dry_run:
            save_state(args.state, state)
        return 3

    # 5. Headlines + why it matters
    summ = summarize(matches, cache) if matches else {}
    new_items = []
    for m in matches:
        s = summ.get(m["id"]) or fallback_summary(m)
        m.update(s)
        filed = datetime.strptime(m["fecha_radicacion"], "%Y-%m-%d").date()
        links = [("View on SUTRA", m["url"])]
        if m["documentos"]:
            links.append((doc_label(m["documentos"][0], original=True), m["documentos"][0]))
        new_items.append({"num": scan_number(m), "date": f"Filed {long_date(filed)}",
                          "headline": s["headline"], "why": s["why"], "title": m["titulo"],
                          "keywords": m["kw_display"], "links": links})

    mov_items = []
    for mv in movement:
        t, m = mv["t"], mv["m"]
        links = [("View on SUTRA", m["url"])]
        doc = (mv["new_docs"] or m["documentos"] or [None])[-1]
        if doc:
            links.append((doc_label(doc, original=not mv["new_docs"]), doc))
        mov_items.append({"num": t["num"],
                          "date": f"Filed {long_date(date.fromisoformat(t['filed']))} · "
                                  f"first sent {long_date(date.fromisoformat(t['first_sent']))}",
                          "headline": t["headline"], "why": t["why"],
                          "news": describe_events(mv["events"]), "links": links})

    # 6. Render + feed
    n = len(new_items)
    title = (f"Legislative Monitor: {n} new measure{'s' if n != 1 else ''}" if n
             else f"Legislative Monitor: {len(mov_items)} update{'s' if len(mov_items) != 1 else ''}")
    subline = f"SUTRA · Measures filed yesterday, {filed_day.strftime('%A')}, {long_date(filed_day)}"
    fragment = email_template.build(new_items, mov_items, title=title, subline=subline, logo_url=LOGO_URL)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sutra.xml").write_text(build_feed(fragment, mail_date, now, title), encoding="utf-8")
    (out / "preview.html").write_text(
        f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title}</title></head><body style="margin:0;background:#f0f0f0;">{fragment}</body></html>',
        encoding="utf-8")
    log(f"wrote {out / 'sutra.xml'}: {n} new, {len(mov_items)} movement, {len(fragment.encode()) // 1024} KB")

    # 7. Remember what was sent
    for m in matches:
        state["tracked"][str(m["id"])] = {
            "num": scan_number(m), "filed": m["fecha_radicacion"], "first_sent": today,
            "headline": m["headline"], "why": m["why"], "events": m["eventos"], "docs": m["documentos"],
            "final": bool(any(FINAL_EVENT.search(ev["evento"]) for ev in m["eventos"]))}
    for mv in movement:
        t, m = mv["t"], mv["m"]
        t["events"], t["docs"] = m["eventos"], m["documentos"]
        t["final"] = any(FINAL_EVENT.search(ev["evento"]) for ev in m["eventos"])
    # Keep "seen" bounded: IDs far below the scan window can never be re-read.
    floor = state["last_max_id"] - 2000
    state["seen"] = {k: v for k, v in state["seen"].items() if int(k) >= floor}
    if not args.dry_run:
        save_state(args.state, state)
    return 0


def scan_number(m):
    """'PS1514' → 'P. del S. 1514', the form used in the email."""
    prefixes = [("RCC", "R. C. de la C."), ("RCS", "R. C. del S."), ("RKC", "R. Conc. de la C."),
                ("RKS", "R. Conc. del S."), ("PC", "P. de la C."), ("PS", "P. del S."),
                ("RC", "R. de la C."), ("RS", "R. del S.")]
    for code, label in prefixes:
        if m["numero"].startswith(code) and m["numero"][len(code):].isdigit():
            return f"{label} {int(m['numero'][len(code):])}"
    return m["numero"]


def main():
    ap = argparse.ArgumentParser(description="Daily SUTRA legislative alert")
    ap.add_argument("--state", default="state/state.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--cache", help="document cache dir (default: a temp dir)")
    ap.add_argument("--now", help="pretend the run happens at this UTC time (ISO 8601), for replays")
    ap.add_argument("--dry-run", action="store_true", help="write the feed but do not update state")
    ap.add_argument("--ignore-cutoff", action="store_true")
    ap.add_argument("--init", action="store_true", help="create a fresh state from live SUTRA and exit")
    ap.add_argument("--start-id", type=int, default=162400, help="--init: scan upward from this ID")
    ap.add_argument("--first-day", help="--init: first filing date (YYYY-MM-DD) the alerts should cover")
    args = ap.parse_args()
    if args.init:
        if not args.first_day:
            sys.exit("--init needs --first-day YYYY-MM-DD")
        init_state(args.state, args.start_id, args.first_day)
        return 0
    if claude_cli.missing():
        log("PREFLIGHT: claude CLI not found; summaries will use fallback text")
    try:
        return run(args)
    except Exception as exc:
        log(f"RUN FAILED: {type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    sys.exit(main())
