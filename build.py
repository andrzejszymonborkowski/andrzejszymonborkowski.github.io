#!/usr/bin/env python3
"""
Generator osobistej strony naukowej.

Źródła publikacji (łączone, bez duplikatów):
  1. ORCID public API  – pobierane automatycznie przy każdym budowaniu
                          (cache w data/orcid_cache.json, gdy API nie odpowiada)
  2. data/extra.bib    – opcjonalny eksport BibTeX z Bazy Wiedzy PW
  3. data/publications_overrides.yaml – ręczne ukrywanie / dopisywanie

Dane ręczne: config.yaml, data/students.yaml, data/projects.yaml, data/service.yaml
Wynik: katalog public/  (gotowy do GitHub Pages)

Użycie:
  python build.py              # pobiera z ORCID i buduje
  python build.py --offline    # bez sieci, tylko cache + extra.bib
  python build.py --demo       # podgląd z przykładowymi publikacjami
"""
import html, json, re, shutil, sys, unicodedata, urllib.request
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "public"
ORCID_API = "https://pub.orcid.org/v3.0"

TYPE_LABELS = {
    "journal": ("Journal articles", "Journal"),
    "conference": ("Conference papers", "Conference"),
    "book": ("Books", "Book"),
    "chapter": ("Book chapters", "Chapter"),
    "preprint": ("Preprints", "Preprint"),
    "data": ("Datasets & software", "Data"),
    "other": ("Other", "Other"),
}
# kolejność ważności przy scalaniu duplikatów (np. preprint + artykuł)
TYPE_RANK = ["journal", "conference", "chapter", "book", "preprint", "data", "other"]
PREPRINT_DOI = ("10.20944/", "10.2139/ssrn", "10.36227/techrxiv", "10.31124/advance",
                "10.48550/arxiv", "10.21203/rs", "10.31219/osf", "10.1101/")
DATA_DOI = ("10.5281/zenodo", "10.71724/", "10.18150/", "10.6084/m9.figshare")
PREPRINT_NAMES = {"10.20944/": "Preprints.org", "10.2139/ssrn": "SSRN",
                  "10.36227/techrxiv": "TechRxiv", "10.31124/advance": "Advance (SAGE)",
                  "10.48550/arxiv": "arXiv", "10.21203/rs": "Research Square"}
ORCID_TYPES = {
    "journal-article": "journal", "journal-issue": "journal", "review": "journal",
    "conference-paper": "conference", "conference-abstract": "conference",
    "conference-poster": "conference", "conference-presentation": "conference",
    "book": "book", "edited-book": "book", "book-chapter": "chapter",
    "preprint": "preprint", "working-paper": "preprint",
    "data-set": "data", "software": "data", "research-tool": "data",
}
BIB_TYPES = {
    "article": "journal", "inproceedings": "conference", "conference": "conference",
    "proceedings": "conference", "book": "book", "incollection": "chapter",
    "inbook": "chapter",
}


# ----------------------------------------------------------------- utils
def esc(s):
    return html.escape(str(s or ""), quote=True)


def load_yaml(path, default=None):
    p = ROOT / path
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or default


def norm_title(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t.lower())[:120]


def norm_doi(d):
    d = (d or "").strip().lower()
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d)


# ----------------------------------------------------------------- ORCID
def http_json(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "academic-site-builder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _v(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def parse_orcid_work(w):
    ext = _v(w, "external-ids", "external-id") or []
    doi = next((e.get("external-id-value") for e in ext
                if e.get("external-id-type") == "doi"), None)
    authors = [_v(c, "credit-name", "value")
               for c in (_v(w, "contributors", "contributor") or [])]
    authors = [a for a in authors if a]
    bib = None
    if _v(w, "citation", "citation-type") == "bibtex":
        bib = _v(w, "citation", "citation-value")
    if not authors and bib:
        m = re.search(r"author\s*=\s*[{\"](.+?)[}\"]\s*,?\s*\n", bib, re.S)
        if m:
            authors = [a.strip() for a in m.group(1).split(" and ")]
    return {
        "title": _v(w, "title", "title", "value") or "",
        "year": int(_v(w, "publication-date", "year", "value") or 0),
        "type": ORCID_TYPES.get(w.get("type"), "other"),
        "orcid_type": w.get("type"),
        "venue": _v(w, "journal-title", "value") or "",
        "doi": norm_doi(doi),
        "url": _v(w, "url", "value") or "",
        "authors": authors,
        "bibtex": bib,
        "source": "ORCID",
    }


def fetch_orcid(orcid):
    summary = http_json(f"{ORCID_API}/{orcid}/works")
    codes = []
    for g in summary.get("group", []):
        ws = g.get("work-summary") or []
        if ws:
            # pierwsza wersja w grupie = preferowana przez właściciela profilu
            codes.append(str(ws[0]["put-code"]))
    works = []
    for i in range(0, len(codes), 100):
        bulk = http_json(f"{ORCID_API}/{orcid}/works/{','.join(codes[i:i+100])}")
        for item in bulk.get("bulk", []):
            if "work" in item:
                works.append(parse_orcid_work(item["work"]))
    return works


def fetch_orcid_person(orcid):
    """Linki z profilu ORCID: strony WWW (np. ResearchGate) i identyfikatory (Scopus, ResearcherID)."""
    person = http_json(f"{ORCID_API}/{orcid}/person")
    links = []
    for u in _v(person, "researcher-urls", "researcher-url") or []:
        links.append({"label": _v(u, "url-name") or "", "url": _v(u, "url", "value") or ""})
    for e in _v(person, "external-identifiers", "external-identifier") or []:
        links.append({"label": e.get("external-id-type") or "", "url": _v(e, "external-id-url", "value") or "",
                      "value": e.get("external-id-value")})
    return [l for l in links if l["url"]]


LINK_NAMES = [("researchgate", "ResearchGate"), ("scopus", "Scopus"), ("researcherid", "Web of Science"),
              ("webofscience", "Web of Science"), ("publons", "Web of Science"), ("wikipedia", "Wikipedia"),
              ("scholar.google", "Google Scholar"), ("linkedin", "LinkedIn")]


def nice_label(l):
    probe = (l.get("url", "") + " " + l.get("label", "")).lower().replace(" ", "")
    return next((n for k, n in LINK_NAMES if k in probe), l.get("label") or l["url"])


# ----------------------------------------------------------------- BibTeX
def parse_bibtex(text):
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{", text):
        etype = m.group(1).lower()
        if etype in ("comment", "string", "preamble"):
            continue
        i, depth = m.end(), 1
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[m.end():i - 1]
        raw = text[m.start():i]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            j = fm.end()
            if j >= len(body):
                continue
            if body[j] == "{":
                d, k = 1, j + 1
                while k < len(body) and d:
                    d += {"{": 1, "}": -1}.get(body[k], 0)
                    k += 1
                val = body[j + 1:k - 1]
            elif body[j] == '"':
                k = body.find('"', j + 1)
                val = body[j + 1:k]
            else:
                val = re.match(r"[^,\n]*", body[j:]).group(0)
            fields[fm.group(1).lower()] = re.sub(r"\s+", " ", val.replace("{", "").replace("}", "")).strip()
        authors = [a.strip() for a in fields.get("author", "").split(" and ") if a.strip()]
        authors = [(" ".join(reversed(a.split(",", 1))).strip() if "," in a else a) for a in authors]
        yr = re.search(r"\d{4}", fields.get("year", ""))
        entries.append({
            "title": fields.get("title", ""),
            "year": int(yr.group(0)) if yr else 0,
            "type": BIB_TYPES.get(etype, "other"),
            "venue": fields.get("journal") or fields.get("booktitle") or fields.get("publisher", ""),
            "doi": norm_doi(fields.get("doi")),
            "url": fields.get("url", ""),
            "authors": authors,
            "bibtex": raw.strip(),
            "source": "BibTeX",
        })
    return entries


# ----------------------------------------------------------------- merge
def classify(p):
    """Doprecyzowuje typ na podstawie prefiksu DOI (preprinty, repozytoria danych)."""
    d = p.get("doi") or ""
    if p["type"] in ("other", "journal", "preprint") and d.startswith(PREPRINT_DOI):
        p["type"] = "preprint"
        name = next((n for k, n in PREPRINT_NAMES.items() if d.startswith(k)), "")
        if name:
            p["venue"] = name
    elif p["type"] == "other" and d.startswith(DATA_DOI):
        p["type"] = "data"
        if d.startswith("10.71724/"):
            p["venue"] = "RepOD / Warsaw University of Technology"
    return p


def _similar(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.9


def merge(*lists):
    seen, out = {}, []
    for lst in lists:
        for p in lst:
            if not p.get("title"):
                continue
            p = classify(dict(p))
            keys = [k for k in (("doi:" + p["doi"]) if p.get("doi") else None,
                                "t:" + norm_title(p["title"])) if k]
            hit = next((seen[k] for k in keys if k in seen), None)
            if hit is None:  # podobny tytuł (±1 rok) = ta sama praca w innej wersji
                nt = norm_title(p["title"])
                hit = next((q for q in out if abs((q.get("year") or 0) - (p.get("year") or 0)) <= 1
                            and q["type"] != "data" and p["type"] != "data"
                            and _similar(norm_title(q["title"]), nt)), None)
                if hit is not None and TYPE_RANK.index(p["type"]) < TYPE_RANK.index(hit["type"]):
                    # nowsza/ważniejsza wersja (np. artykuł zamiast preprintu) wygrywa
                    for f in ("title", "type", "venue", "doi", "url", "year", "bibtex"):
                        if p.get(f):
                            hit[f] = p[f]
            if hit is None:
                out.append(p)
                for k in keys:
                    seen[k] = p
            else:  # uzupełnij brakujące pola z drugiego źródła
                for f in ("authors", "venue", "doi", "url", "bibtex", "year"):
                    if not hit.get(f) and p.get(f):
                        hit[f] = p[f]
                if len(p.get("authors", [])) > len(hit.get("authors", [])):
                    hit["authors"] = p["authors"]
                for k in keys:
                    seen.setdefault(k, hit)
    return out


def apply_overrides(pubs, ov):
    hide = [h.lower() for h in (ov or {}).get("hide", [])]
    pubs = [p for p in pubs if not any(
        h == p.get("doi") or h in p["title"].lower() for h in hide)]
    for m in (ov or {}).get("manual", []):
        m.setdefault("authors", [])
        if isinstance(m["authors"], str):
            m["authors"] = [a.strip() for a in m["authors"].split(",")]
        m["doi"] = norm_doi(m.get("doi"))
        m.setdefault("type", "other")
        m.setdefault("source", "manual")
        pubs.append(m)
    return pubs


def make_bibtex(p):
    if p.get("bibtex"):
        return p["bibtex"]
    first = (p["authors"][0].split()[-1] if p["authors"] else "anon")
    key = re.sub(r"\W", "", unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode()) + str(p["year"] or "")
    et = {"journal": "article", "conference": "inproceedings", "book": "book",
          "chapter": "incollection"}.get(p["type"], "misc")
    vf = {"journal": "journal", "conference": "booktitle", "chapter": "booktitle"}.get(p["type"], "publisher")
    lines = [f"@{et}{{{key},",
             f"  title = {{{p['title']}}},",
             f"  author = {{{' and '.join(p['authors'])}}},"]
    if p.get("venue"):
        lines.append(f"  {vf} = {{{p['venue']}}},")
    if p.get("year"):
        lines.append(f"  year = {{{p['year']}}},")
    if p.get("doi"):
        lines.append(f"  doi = {{{p['doi']}}},")
    lines.append("}")
    return "\n".join(lines)


# ----------------------------------------------------------------- HTML
I18N = {
    "en": dict(home="Home", pubs="Publications", students="Students", projects="Projects",
               service="Service", interests="Research interests", updated="Last updated",
               search="Search titles, authors, venues…", all="All types", phd="Doctoral candidates",
               msc="Master's theses", beng="Engineering theses", intern="Interns",
               current="current", completed="completed", ongoing="Ongoing", done="Completed",
               role="Role", total="publications", source="Publication list synchronised daily with ORCID.",
               copy="BibTeX", copied="Copied!", contact="Contact", nores="No matching publications.", peer="publications",
               stud_sub="Doctoral candidates and thesis students", proj_sub="Research and development projects",
               serv_sub="Editorial work, committees and professional activity"),
    "pl": dict(home="Strona główna", pubs="Publikacje", students="Studenci", projects="Projekty",
               service="Działalność", interests="Zainteresowania badawcze", updated="Aktualizacja",
               search="Szukaj w tytułach, autorach, czasopismach…", all="Wszystkie typy",
               phd="Doktoranci", msc="Prace magisterskie", beng="Prace inżynierskie", intern="Stażyści",
               current="w toku", completed="zakończone", ongoing="W realizacji", done="Zakończone",
               role="Rola", total="publikacji", source="Lista publikacji synchronizowana codziennie z ORCID.",
               copy="BibTeX", copied="Skopiowano!", contact="Kontakt", nores="Brak pasujących publikacji.", peer="publikacji",
               stud_sub="Doktoranci i dyplomanci", proj_sub="Projekty badawcze i rozwojowe",
               serv_sub="Redakcje, komitety i działalność organizacyjna"),
}

CSS = """
/* Styl spójny z prezentacjami konferencyjnymi: granat, błękit, złoto; Georgia + Calibri */
:root{--navy:#1A1F36;--primary:#0C5C8F;--accent:#18A0D6;--gold:#E8A838;--light:#F0F4F8;--white:#fff;
--muted:#6B7B8D;--text:#1E293B;--ltext:#CBD5E1;--line:#DCE3EA;--teal:#2A9D8F;
--shadow:0 3px 14px rgba(0,0,0,.08);--shadow-h:0 8px 24px rgba(12,92,143,.16);
--serif:Georgia,"Times New Roman",serif;--sans:Calibri,Carlito,"Segoe UI",system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--light);color:var(--text);font:17px/1.6 var(--sans)}
a{color:var(--primary);text-decoration:none}
a:hover{color:var(--accent)}
.wrap{max-width:1040px;margin:0 auto;padding:0 28px}
h1,h2,h3{font-family:var(--serif);font-weight:700;line-height:1.2;color:var(--navy)}
/* --- nagłówek --- */
header.site{background:var(--navy);border-top:5px solid var(--accent);position:sticky;top:0;z-index:10;box-shadow:0 2px 10px rgba(0,0,0,.25)}
header.site .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;padding-top:14px;padding-bottom:14px}
.brand{font-family:var(--serif);font-size:1.12rem;font-weight:700;color:var(--white)}
.brand:hover{color:var(--white)}
.brand span{color:var(--gold)}
nav{display:flex;flex-wrap:wrap;gap:4px 26px}
nav a{color:var(--ltext);font-size:.98rem;padding:4px 0;border-bottom:2px solid transparent;transition:color .15s,border-color .15s}
nav a:hover{color:var(--white)}
nav a.active{color:var(--white);border-bottom-color:var(--gold)}
/* --- ciemny pas (jak slajd tytułowy) --- */
.band{background:var(--navy);color:var(--white);position:relative;overflow:hidden}
.band::after{content:"";position:absolute;right:-120px;top:-120px;width:420px;height:420px;border-radius:50%;
 background:radial-gradient(circle,rgba(24,160,214,.22),rgba(24,160,214,0) 70%);pointer-events:none}
.band .wrap{position:relative;z-index:1}
.goldbar{width:72px;height:4px;background:var(--gold);margin:0 0 18px}
.band.small .wrap{padding-top:44px;padding-bottom:40px}
.band.small h1{color:var(--white);font-size:2.3rem;margin:0}
.band.small p{color:var(--accent);font-style:italic;margin:8px 0 0;font-size:1.05rem}
.hero .wrap{display:grid;grid-template-columns:190px 1fr;gap:44px;align-items:center;padding-top:56px;padding-bottom:96px}
.hero img,.hero .ph{width:190px;height:230px;object-fit:cover;border-radius:6px;border:3px solid var(--gold);box-shadow:0 10px 30px rgba(0,0,0,.35)}
.hero .ph{display:flex;align-items:center;justify-content:center;font-family:var(--serif);font-size:3.2rem;color:var(--gold);background:#232A47}
.hero h1{color:var(--white);font-size:2.5rem;margin:0}
.hero .title{color:var(--accent);font-style:italic;font-size:1.15rem;margin:8px 0 14px}
.hero .aff{color:var(--ltext);margin:0;line-height:1.5}
.links{margin-top:22px;display:flex;gap:8px;flex-wrap:wrap}
.links a{font-size:.88rem;color:var(--white);border:1px solid rgba(203,213,225,.35);background:rgba(255,255,255,.05);padding:5px 14px;border-radius:999px;transition:all .15s}
.links a:hover{border-color:var(--gold);color:var(--gold);background:rgba(232,168,56,.08)}
/* --- statystyki (karty z kolorowym paskiem) --- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin:-58px 0 8px;position:relative;z-index:2}
.stat{background:var(--white);border-radius:6px;box-shadow:var(--shadow);padding:18px 18px 16px;border-top:4px solid var(--primary)}
.stat:nth-child(2){border-top-color:var(--accent)}.stat:nth-child(3){border-top-color:var(--gold)}
.stat:nth-child(4){border-top-color:var(--teal)}.stat:nth-child(5){border-top-color:var(--navy)}
.stat b{display:block;font-family:var(--serif);font-size:2.1rem;color:var(--navy);line-height:1.1}
.stat span{color:var(--muted);font-size:.9rem}
body{min-height:100vh;display:flex;flex-direction:column}
main{padding:40px 0 72px;flex:1}
h2{font-size:1.55rem;margin:48px 0 20px}
h2::after{content:"";display:block;width:56px;height:4px;background:var(--gold);margin-top:10px}
.lead{font-size:1.08rem;max-width:780px}
.chips{display:flex;flex-wrap:wrap;gap:10px;padding:0;list-style:none;margin:0}
.chips li{background:var(--white);box-shadow:var(--shadow);border-left:4px solid var(--accent);padding:9px 16px;border-radius:4px;font-size:.97rem}
.more{font-family:var(--sans);font-size:.95rem;font-weight:400;margin-left:10px}
/* --- publikacje --- */
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 6px;position:sticky;top:64px;background:var(--light);padding:14px 0;z-index:5}
.toolbar input,.toolbar select{font:inherit;font-size:.97rem;padding:10px 14px;border:1px solid var(--line);border-radius:6px;background:var(--white);color:var(--text);box-shadow:var(--shadow)}
.toolbar input:focus,.toolbar select:focus{outline:2px solid var(--accent);outline-offset:1px}
.toolbar input{flex:1;min-width:220px}
.years{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 6px}
.years a{color:var(--primary);background:var(--white);box-shadow:var(--shadow);padding:3px 11px;border-radius:999px;font-size:.88rem}
.years a:hover{background:var(--gold);color:var(--navy)}
.pub{background:var(--white);border-radius:6px;box-shadow:var(--shadow);padding:16px 20px 14px;margin:0 0 12px;border-left:4px solid var(--primary);transition:box-shadow .15s,transform .15s}
.pub:hover{box-shadow:var(--shadow-h);transform:translateY(-1px)}
.pub[data-t=conference]{border-left-color:var(--accent)}
.pub[data-t=chapter],.pub[data-t=book]{border-left-color:var(--gold)}
.pub[data-t=preprint]{border-left-color:var(--muted)}
.pub[data-t=data]{border-left-color:var(--teal)}
.pub .t{font-weight:700;color:var(--navy);font-size:1.03rem;line-height:1.35}
.pub .a{font-size:.95rem;margin-top:3px}
.pub .a .me{font-weight:700;color:var(--primary)}
.pub .v{color:var(--muted);font-size:.93rem;font-style:italic}
.pub .m{margin-top:8px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:.85rem}
.badge{font-size:.7rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 9px;border-radius:3px;background:var(--primary);color:var(--white)}
.badge.conference{background:var(--accent)}.badge.chapter,.badge.book{background:var(--gold);color:var(--navy)}
.badge.preprint{background:var(--muted)}.badge.data{background:var(--teal)}
.m a{font-weight:700}
.btn{font:inherit;font-size:.85rem;font-weight:700;border:none;background:none;color:var(--muted);cursor:pointer;padding:0}
.btn:hover{color:var(--accent)}
pre.bib{display:none;background:var(--navy);color:var(--ltext);padding:12px 14px;border-radius:6px;font-size:.78rem;overflow:auto;white-space:pre-wrap;margin:10px 0 0}
pre.bib.open{display:block}
.year-h{scroll-margin-top:150px}
.empty{color:var(--muted);display:none;padding:20px 0}
/* --- karty (studenci, projekty) --- */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.card{background:var(--white);border-radius:6px;box-shadow:var(--shadow);padding:18px 20px;border-top:4px solid var(--primary)}
.card.done{border-top-color:var(--gold)}
.card .t{font-family:var(--serif);font-weight:700;color:var(--navy);font-size:1.1rem;line-height:1.3}
.card .topic{margin-top:6px}
.card .meta{color:var(--muted);font-size:.92rem;margin-top:6px}
.pill{display:inline-block;font-family:var(--sans);font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:2px 9px;border-radius:3px;background:var(--light);color:var(--muted);margin-top:8px}
.pill.on{background:var(--accent);color:var(--white)}
table.s{width:100%;border-collapse:separate;border-spacing:0 8px}
table.s td{background:var(--white);padding:13px 18px;vertical-align:top;box-shadow:var(--shadow)}
table.s td:first-child{border-left:4px solid var(--gold);border-radius:6px 0 0 6px}
table.s td:last-child{color:var(--muted);white-space:nowrap;text-align:right;border-radius:0 6px 6px 0}
footer{background:var(--navy);color:var(--ltext);font-size:.85rem;padding:22px 0;border-top:4px solid var(--gold)}
footer .wrap{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.demo{background:#FFF6E0;border:1px solid var(--gold);padding:10px 14px;border-radius:6px;font-size:.9rem;margin-bottom:24px}
@media (max-width:720px){.hero .wrap{grid-template-columns:1fr;gap:24px;padding-top:36px}.hero img,.hero .ph{width:140px;height:170px}
.hero h1{font-size:1.9rem}nav{gap:4px 16px}.band.small h1{font-size:1.8rem}.toolbar{top:0;position:static}}
"""

JS = """
(function(){
 const q=document.getElementById('q'),t=document.getElementById('t');
 if(!q)return;
 const items=[...document.querySelectorAll('.pub')],heads=[...document.querySelectorAll('.year-h')],empty=document.querySelector('.empty');
 function run(){
  const s=q.value.trim().toLowerCase(),ty=t.value;let n=0;
  items.forEach(el=>{const ok=(!s||el.dataset.s.includes(s))&&(!ty||el.dataset.t===ty);el.hidden=!ok;if(ok)n++;});
  heads.forEach(h=>{const y=h.dataset.y;h.hidden=!items.some(el=>el.dataset.y===y&&!el.hidden);});
  empty.style.display=n?'none':'block';
 }
 q.addEventListener('input',run);t.addEventListener('change',run);
 document.addEventListener('click',e=>{
  const b=e.target.closest('.btn[data-bib]');if(!b)return;
  const pre=document.getElementById(b.dataset.bib);pre.classList.toggle('open');
  if(pre.classList.contains('open')&&navigator.clipboard){navigator.clipboard.writeText(pre.textContent).then(()=>{const o=b.textContent;b.textContent=b.dataset.ok;setTimeout(()=>b.textContent=o,1200);}).catch(()=>{});}
 });
})();
"""


def page(cfg, L, active, title, body, demo=False, band=""):
    tabs = [("index.html", L["home"]), ("publications.html", L["pubs"]),
            ("students.html", L["students"]), ("projects.html", L["projects"]),
            ("service.html", L["service"])]
    nav = "".join(f'<a href="{h}"{" class=active" if h == active else ""}>{esc(n)}</a>' for h, n in tabs)
    banner = ('<div class="demo">Podgląd z <b>przykładowymi</b> publikacjami – po pierwszym '
              'uruchomieniu na GitHubie lista zostanie pobrana z ORCID.</div>') if demo else ""
    return f"""<!DOCTYPE html>
<html lang="{cfg.get('lang','en')}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} – {esc(cfg['name'])}</title>
<meta name="description" content="{esc(cfg['name'])}, {esc(cfg.get('affiliation',''))}">
<link rel="stylesheet" href="style.css"></head><body>
<header class="site"><div class="wrap"><a class="brand" href="index.html">{esc(cfg['name'])}</a><nav>{nav}</nav></div></header>
{band}
<main><div class="wrap">{banner}{body}</div></main>
<footer><div class="wrap"><span>© {date.today().year} {esc(cfg['name'])} · {esc(cfg.get('affiliation',''))}</span><span>{L['updated']}: {date.today().isoformat()}</span></div></footer>
<script src="site.js"></script></body></html>"""


def small_band(title, sub=""):
    return (f'<section class="band small"><div class="wrap"><div class="goldbar"></div><h1>{esc(title)}</h1>'
            + (f'<p>{sub}</p>' if sub else "") + '</div></section>')


def fmt_authors(authors, me_patterns):
    out = []
    for a in authors:
        s = esc(a)
        if any(re.search(pat, a, re.I) for pat in me_patterns):
            s = f'<span class="me">{s}</span>'
        out.append(s)
    return ", ".join(out)


def render_pub(p, idx, me, L):
    links = []
    if p.get("doi"):
        links.append(f'<a href="https://doi.org/{esc(p["doi"])}">DOI</a>')
    elif p.get("url"):
        links.append(f'<a href="{esc(p["url"])}">Link</a>')
    bid = f"bib{idx}"
    search = " ".join([p["title"], " ".join(p["authors"]), p.get("venue", ""), str(p["year"])]).lower()
    return f"""<div class="pub" data-y="{p['year']}" data-t="{p['type']}" data-s="{esc(search)}">
<div class="t">{esc(p['title'])}</div>
<div class="a">{fmt_authors(p['authors'], me)}</div>
{f'<div class="v">{esc(p["venue"])}</div>' if p.get('venue') else ''}
<div class="m"><span class="badge {p['type']}">{TYPE_LABELS[p['type']][1]}</span>{' '.join(links)}
<button class="btn" data-bib="{bid}" data-ok="{L['copied']}">{L['copy']}</button></div>
<pre class="bib" id="{bid}">{esc(make_bibtex(p))}</pre></div>"""


def build(demo=False, offline=False):
    cfg = load_yaml("config.yaml", {})
    L = I18N.get(cfg.get("lang", "en"), I18N["en"])
    students = load_yaml("data/students.yaml", []) or []
    projects = load_yaml("data/projects.yaml", []) or []
    service = load_yaml("data/service.yaml", []) or []
    overrides = load_yaml("data/publications_overrides.yaml", {}) or {}
    me = cfg.get("highlight_name_patterns") or [r"Borkowski"]

    # ---- publikacje
    cache = DATA / "orcid_cache.json"
    orcid_pubs = []
    if demo:
        orcid_pubs = json.loads((DATA / "demo_publications.json").read_text(encoding="utf-8"))
    elif not offline and cfg.get("orcid"):
        try:
            orcid_pubs = fetch_orcid(cfg["orcid"])
            cache.write_text(json.dumps(orcid_pubs, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"ORCID: pobrano {len(orcid_pubs)} prac")
        except Exception as e:
            print(f"ORCID niedostępny ({e}) – używam cache", file=sys.stderr)
    if not orcid_pubs and cache.exists() and not demo:
        orcid_pubs = json.loads(cache.read_text(encoding="utf-8"))
    person_cache = DATA / "orcid_person.json"
    if not demo and not offline and cfg.get("orcid"):
        try:
            person_cache.write_text(json.dumps(fetch_orcid_person(cfg["orcid"]), ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            print(f"ORCID person niedostępny ({e})", file=sys.stderr)
    auto_links = json.loads(person_cache.read_text(encoding="utf-8")) if person_cache.exists() else []

    bib_pubs = []
    bibfile = DATA / "extra.bib"
    if bibfile.exists():
        bib_pubs = parse_bibtex(bibfile.read_text(encoding="utf-8", errors="replace"))
        print(f"BibTeX: {len(bib_pubs)} pozycji")
    pubs = apply_overrides(merge(orcid_pubs, bib_pubs), overrides)
    pubs.sort(key=lambda p: (-(p.get("year") or 0), TYPE_RANK.index(p["type"]), p["title"].lower()))

    OUT.mkdir(exist_ok=True)
    (OUT / "style.css").write_text(CSS, encoding="utf-8")
    (OUT / "site.js").write_text(JS, encoding="utf-8")
    if (ROOT / "assets").exists():
        shutil.copytree(ROOT / "assets", OUT / "assets", dirs_exist_ok=True)

    # ---- strona główna
    photo = cfg.get("photo")
    if photo and (ROOT / photo).exists():
        img = f'<img src="{esc(photo)}" alt="{esc(cfg["name"])}">'
    else:
        initials = "".join(w[0] for w in cfg["name"].split()[::2])[:2]
        img = f'<div class="ph">{esc(initials)}</div>'
    all_links, seen_l = [], set()
    for l in list(cfg.get("links", [])) + [dict(l, label=nice_label(l)) for l in auto_links]:
        key = l["url"].rstrip("/").lower()
        if key not in seen_l and l["label"] not in [x["label"] for x in all_links]:
            seen_l.add(key); all_links.append(l)
    links = "".join(f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in all_links)
    links += f'<a href="mailto:{esc(cfg.get("email"))}">{esc(cfg.get("email"))}</a>' if cfg.get("email") else ""
    n_j = sum(1 for p in pubs if p["type"] == "journal")
    n_pub = sum(1 for p in pubs if p["type"] not in ("data", "preprint", "other"))
    n_data = sum(1 for p in pubs if p["type"] == "data")
    n_phd = sum(1 for s in students if s.get("level") == "phd")
    n_proj = len(projects)
    recent = "".join(render_pub(p, f"h{i}", me, L) for i, p in enumerate([q for q in pubs if q["type"] not in ("data", "preprint")][:5]))
    interests = "".join(f"<li>{esc(i)}</li>" for i in cfg.get("research_interests", []))
    hero = f"""<section class="band hero"><div class="wrap">{img}<div>
<div class="goldbar"></div>
<h1>{esc(cfg['name'])}</h1>
<p class="title">{esc(cfg.get('title',''))} · {esc(cfg.get('position',''))}</p>
<p class="aff">{esc(cfg.get('unit',''))}<br>{esc(cfg.get('affiliation',''))}</p>
<div class="links">{links}</div>
</div></div></section>"""
    home = f"""<div class="stats"><div class="stat"><b>{n_pub}</b><span>{L['peer']}</span></div>
<div class="stat"><b>{n_j}</b><span>{TYPE_LABELS['journal'][0].lower()}</span></div>
<div class="stat"><b>{n_data}</b><span>{TYPE_LABELS['data'][0].lower()}</span></div>
<div class="stat"><b>{n_phd}</b><span>{L['phd'].lower()}</span></div>
<div class="stat"><b>{n_proj}</b><span>{L['projects'].lower()}</span></div></div>
<h2>About</h2><p class="lead">{esc(cfg.get('bio',''))}</p>
<h2>{L['interests']}</h2><ul class="chips">{interests}</ul>
<h2>Recent {L['pubs'].lower()}<a class="more" href="publications.html">→ all</a></h2>{recent}
<div class="empty"></div>"""
    (OUT / "index.html").write_text(page(cfg, L, "index.html", L["home"], home, demo, hero), encoding="utf-8")

    # ---- publikacje
    years = sorted({p["year"] for p in pubs}, reverse=True)
    types_present = [k for k in TYPE_LABELS if any(p["type"] == k for p in pubs)]
    opts = f'<option value="">{L["all"]}</option>' + "".join(
        f'<option value="{k}">{TYPE_LABELS[k][0]} ({sum(1 for p in pubs if p["type"]==k)})</option>' for k in types_present)
    ylinks = "".join(f'<a href="#y{y}">{y or "n.d."}</a>' for y in years)
    blocks, idx = [], 0
    for y in years:
        blocks.append(f'<h2 class="year-h" id="y{y}" data-y="{y}">{y or "n.d."}</h2>')
        for p in (p for p in pubs if p["year"] == y):
            blocks.append(render_pub(p, idx, me, L)); idx += 1
    pubs_html = f"""<div class="years">{ylinks}</div>
<div class="toolbar"><input id="q" type="search" placeholder="{L['search']}"><select id="t">{opts}</select></div>
{''.join(blocks)}<div class="empty">{L['nores']}</div>"""
    (OUT / "publications.html").write_text(page(cfg, L, "publications.html", L["pubs"], pubs_html, demo,
         small_band(L["pubs"], f"{len(pubs)} {L['total']} · {L['source']}")), encoding="utf-8")

    # ---- studenci
    sh = []
    for lvl in ("phd", "msc", "beng", "intern"):
        group = [s for s in students if s.get("level") == lvl]
        if not group:
            continue
        group.sort(key=lambda s: (s.get("status") != "current", -(s.get("year") or 9999)))
        sh.append(f"<h2>{L[lvl]}</h2><div class='cards'>")
        for s in group:
            st = s.get("status", "current")
            pill = f'<span class="pill{" on" if st=="current" else ""}">{L[st]}{(" " + str(s["year"])) if s.get("year") else ""}</span>'
            name = f'<a href="{esc(s["url"])}">{esc(s["name"])}</a>' if s.get("url") else esc(s["name"])
            sh.append(f'<div class="card{"" if st=="current" else " done"}"><div class="t">{name}</div>'
                      + (f'<div class="topic">{esc(s["topic"])}</div>' if s.get("topic") else "")
                      + (f'<div class="meta">{L["role"]}: {esc(s["role"])}</div>' if s.get("role") else "")
                      + pill + "</div>")
        sh.append("</div>")
    (OUT / "students.html").write_text(page(cfg, L, "students.html", L["students"], "".join(sh), demo, small_band(L["students"], L["stud_sub"])), encoding="utf-8")

    # ---- projekty
    ph = []
    for st, label in (("ongoing", L["ongoing"]), ("completed", L["done"])):
        group = [p for p in projects if p.get("status", "completed") == st]
        if not group:
            continue
        ph.append(f"<h2>{label}</h2><div class='cards'>")
        for p in group:
            t = f'<a href="{esc(p["url"])}">{esc(p["title"])}</a>' if p.get("url") else esc(p["title"])
            meta = " · ".join(esc(x) for x in (p.get("funder"), p.get("years"),
                              f'{L["role"]}: {p["role"]}' if p.get("role") else None, p.get("partners")) if x)
            ph.append(f'<div class="card{"" if st=="ongoing" else " done"}"><div class="t">{t}</div><div class="meta">{meta}</div>'
                      + (f'<p style="margin:8px 0 0">{esc(p["description"])}</p>' if p.get("description") else "")
                      + "</div>")
        ph.append("</div>")
    (OUT / "projects.html").write_text(page(cfg, L, "projects.html", L["projects"], "".join(ph), demo, small_band(L["projects"], L["proj_sub"])), encoding="utf-8")

    # ---- działalność
    vh = []
    for g in service:
        vh.append(f"<h2>{esc(g['category'])}</h2><table class='s'>")
        for it in g.get("items", []):
            vh.append(f"<tr><td>{esc(it['text'])}</td><td>{esc(it.get('years',''))}</td></tr>")
        vh.append("</table>")
    (OUT / "service.html").write_text(page(cfg, L, "service.html", L["service"], "".join(vh), demo, small_band(L["service"], L["serv_sub"])), encoding="utf-8")

    print(f"Gotowe: {len(pubs)} publikacji → {OUT}/")


if __name__ == "__main__":
    build(demo="--demo" in sys.argv, offline="--offline" in sys.argv)
