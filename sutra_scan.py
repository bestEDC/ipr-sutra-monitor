"""Scan SUTRA (sutra.oslpr.org) measures by ID and flag ones matching InvestPR keywords.

Usage: python3 sutra_scan.py --since 2026-09-01 [--until 2026-09-30] [--start-id 162650] [--out data/scan.json]
"""
import argparse
import html
import json
import os
import subprocess
import re
import shutil
import sys
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

BASE = "https://sutra.oslpr.org"

KEYWORDS = [
    "desarrollo económico",
    "inversión",
    "Invest Puerto Rico",
    "Ley 60",
    "código de incentivos",
    "incentivos",
    "DDEC",
    "Departamento de Desarrollo Económico y Comercio",
    "Department of Economic Development and Commerce",
]

# Proyectos and resoluciones only (excludes nombramientos, planes, informes, etc.)
TYPE_RE = re.compile(r"^(Proyecto|Resoluci[oó]n)", re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower()


def keyword_patterns():
    pats = {}
    for kw in KEYWORDS:
        k = re.escape(norm(kw))
        if kw == "Ley 60":
            # Ley 60, Ley 60-2019, Ley Núm. 60 — but not Ley 600
            pats[kw] = re.compile(r"\bley (num\.? |numero )?60\b(?!\d)")
        elif kw == "inversión":
            pats[kw] = re.compile(r"\binversion(es)?\b")
        elif kw == "incentivos":
            pats[kw] = re.compile(r"\bincentivos?\b")
        else:
            pats[kw] = re.compile(r"\b" + k + r"\b")
    return pats


PATTERNS = keyword_patterns()


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "InvestPR-SUTRA-Monitor/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_measure(mid, raw):
    body = re.sub(r"<script[\s\S]*?</script>", "", raw)
    txt = html.unescape(re.sub(r"<[^>]+>", " | ", body))
    txt = re.sub(r"(\s*\|\s*)+", " | ", txt)
    m = re.search(r"Trámites \| (.+?) \((\w+)\) \| Cuatrienio: \| ([\d-]+) \| Fecha de Radicación: \| ([\d/]+) \| Título: \| (.+?) \| Select a tab", txt, re.S)
    if not m:
        return None
    tipo, numero, cuatrienio, fecha, titulo = m.groups()
    fecha_iso = datetime.strptime(fecha, "%m/%d/%Y").date().isoformat()
    authors = []
    for a in re.findall(r'/legisladores/M-\d+-\d+\\",\\"children\\":\[\[[^\]]*\],\\"\$undefined\\",\\" \\",\\"([^"\\]+)\\"', raw):
        a = re.sub(r"\s+", " ", a).strip()
        if a not in authors:
            authors.append(a)
    docs = []
    for d in re.findall(r"(/SutraFilesGen/\d+/[^\"\\]+?\.(?:pdf|docx?))", raw):
        u = BASE + d.replace(" ", "%20")
        if u not in docs:
            docs.append(u)
    events = re.findall(r"\| ([A-ZÁÉÍÓÚ][^|]{2,80}?) \| Fecha: \| ([\d/]+)", txt)
    return {
        "id": mid,
        "url": f"{BASE}/medidas/{mid}",
        "tipo": tipo.strip(),
        "numero": numero,
        "cuatrienio": cuatrienio,
        "fecha_radicacion": fecha_iso,
        "titulo": re.sub(r"\s+", " ", titulo).strip(),
        "autores": authors,
        "documentos": docs,
        "eventos": [{"evento": e.strip(), "fecha": f} for e, f in events],
    }


def match_keywords(title):
    t = norm(title)
    return [kw for kw, p in PATTERNS.items() if p.search(t)]


def scan(since, start_id, until="9999-12-31", workers=6, stop_after=80):
    """Walk IDs downward from start_id until `stop_after` consecutive measures predate `since`.
    Measures filed after `until` are skipped. SUTRA interleaves digitized historical records
    (e.g. 1990 measures) in the ID sequence; anything filed >1 year before `since` is ignored
    so it doesn't end the scan early."""
    archive_cutoff = f"{int(since[:4]) - 1}{since[4:]}"
    results, older_streak, mid = [], 0, start_id
    with ThreadPoolExecutor(workers) as ex:
        while older_streak < stop_after and mid > 0:
            batch = list(range(mid, max(mid - workers * 2, 0), -1))
            mid = batch[-1] - 1

            def get(i):
                try:
                    return parse_measure(i, fetch(f"{BASE}/medidas/{i}"))
                except Exception as e:
                    print(f"  ! {i}: {e}", file=sys.stderr)
                    return None

            for m in ex.map(get, batch):
                if not m or m["fecha_radicacion"] < archive_cutoff:
                    continue
                if m["fecha_radicacion"] < since:
                    older_streak += 1
                    continue
                older_streak = 0
                if m["fecha_radicacion"] <= until:
                    results.append(m)
            print(f"  scanned down to {mid}, kept {len(results)}", file=sys.stderr)
    return results


def fetch_document_text(m, cache_dir):
    """Download the measure's first document (pdf/docx) and return its plain text, or None."""
    if not m["documentos"]:
        return None
    url = m["documentos"][0]
    ext = url.rsplit(".", 1)[1].lower()
    src = os.path.join(cache_dir, f"{m['numero']}.{ext}")
    txt = os.path.join(cache_dir, f"{m['numero']}.txt")
    if not os.path.exists(src):
        req = urllib.request.Request(url, headers={"User-Agent": "InvestPR-SUTRA-Monitor/0.1"})
        with urllib.request.urlopen(req, timeout=60) as r, open(src, "wb") as f:
            f.write(r.read())
    if ext == "pdf":
        subprocess.run(["pdftotext", "-layout", src, txt], check=True, capture_output=True)
    elif ext == "docx":
        import docx2txt  # pure Python, so it runs the same on macOS and the Linux runner
        with open(txt, "w") as f:
            f.write(docx2txt.process(src) or "")
    elif shutil.which("antiword"):  # legacy .doc on Linux
        with open(txt, "w") as f:
            f.write(subprocess.run(["antiword", src], check=True, capture_output=True, text=True).stdout)
    else:  # legacy .doc on macOS
        subprocess.run(["textutil", "-convert", "txt", "-output", txt, src], check=True, capture_output=True)
    return open(txt, errors="replace").read()


def add_fulltext_keywords(measures, cache_dir, workers=5):
    os.makedirs(cache_dir, exist_ok=True)
    in_scope = [m for m in measures if m["is_proyecto_o_resolucion"]]

    def get(m):
        try:
            return m, fetch_document_text(m, cache_dir), None
        except Exception as e:
            return m, None, str(e)

    with ThreadPoolExecutor(workers) as ex:
        for m, txt, err in ex.map(get, in_scope):
            if err:
                m["texto_status"], m["keywords_texto"] = f"error: {err}", []
            elif txt is None:
                m["texto_status"], m["keywords_texto"] = "sin documento", []
            else:
                m["texto_status"] = "ok" if len(txt.split()) > 50 else "texto vacío/escaneado"
                m["palabras"] = len(txt.split())
                nt = norm(txt)
                m["keywords_texto"] = [kw for kw, p in PATTERNS.items() if p.search(nt)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD")
    ap.add_argument("--until", default="9999-12-31", help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--start-id", type=int, default=162650)
    ap.add_argument("--docs-dir", default="data/docs", help="cache for downloaded documents (full-text search)")
    ap.add_argument("--no-fulltext", action="store_true")
    ap.add_argument("--out", default="data/scan.json")
    a = ap.parse_args()
    measures = scan(a.since, a.start_id, a.until)
    for m in measures:
        m["is_proyecto_o_resolucion"] = bool(TYPE_RE.match(m["tipo"]))
        m["keywords"] = match_keywords(m["titulo"])
    measures.sort(key=lambda m: m["id"])
    if not a.no_fulltext:
        add_fulltext_keywords(measures, a.docs_dir)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(measures, f, ensure_ascii=False, indent=1)
    hits = [m for m in measures if m["is_proyecto_o_resolucion"] and m["keywords"]]
    print(f"{len(measures)} measures since {a.since}; {len(hits)} keyword hits", file=sys.stderr)
    for m in hits:
        print(f"{m['numero']:>10}  {m['fecha_radicacion']}  {', '.join(m['keywords'])}  | {m['titulo'][:110]}")


if __name__ == "__main__":
    main()
