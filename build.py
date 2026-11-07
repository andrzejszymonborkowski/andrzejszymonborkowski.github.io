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
               copy="BibTeX", copied="Copied!", contact="Contact", nores="No matching publications.", peer="publications"),
    "pl": dict(home="Strona główna", pubs="Publikacje", students="Studenci", projects="Projekty",
               service="Działalność", interests="Zainteresowania badawcze", updated="Aktualizacja",
               search="Szukaj w tytułach, autorach, czasopismach…", all="Wszystkie typy",
               phd="Doktoranci", msc="Prace magisterskie", beng="Prace inżynierskie", intern="Stażyści",
               current="w toku", completed="zakończone", ongoing="W realizacji", done="Zakończone",
               role="Rola", total="publikacji", source="Lista publikacji synchronizowana codziennie z ORCID.",
               copy="BibTeX", copied="Skopiowano!", contact="Kontakt", nores="Brak pasujących publikacji.", peer="publikacji"),
}

CSS = """
:root{--ink:#1d2433;--muted:#5d6676;--line:#e4e1da;--bg:#fbfaf7;--card:#fff;--accent:#8a1c2b;--accent-soft:#f4e7e8;--mark:#fff3c4}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:2px}
.wrap{max-width:960px;margin:0 auto;padding:0 24px}
header.site{border-bottom:1px solid var(--line);background:var(--card)}
header.site .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;padding-top:18px;padding-bottom:18px}
.brand{font-family:Charter,"Iowan Old Style","Source Serif Pro",Georgia,serif;font-size:1.15rem;font-weight:600;color:var(--ink);text-decoration:none}
nav{display:flex;flex-wrap:wrap;gap:4px 20px}
nav a{color:var(--muted);text-decoration:none;font-size:.95rem;padding:4px 0;border-bottom:2px solid transparent}
nav a:hover{color:var(--ink)}
nav a.active{color:var(--ink);border-bottom-color:var(--accent)}
main{padding:44px 0 64px}
h1,h2,h3{font-family:Charter,"Iowan Old Style","Source Serif Pro",Georgia,serif;font-weight:600;line-height:1.25}
h1{font-size:2rem;margin:0 0 6px}
h2{font-size:1.35rem;margin:40px 0 14px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:1.05rem;margin:26px 0 8px;color:var(--muted)}
.hero{display:grid;grid-template-columns:150px 1fr;gap:32px;align-items:start}
.hero img{width:150px;height:180px;object-fit:cover;border-radius:4px;background:var(--line)}
.ph{width:150px;height:180px;border-radius:4px;background:var(--line);display:flex;align-items:center;justify-content:center;font-family:Georgia,serif;font-size:2.4rem;color:var(--muted)}
.sub{color:var(--muted);margin:0}
.links{margin-top:14px;display:flex;gap:10px;flex-wrap:wrap}
.links a{font-size:.88rem;border:1px solid var(--line);background:var(--card);padding:4px 12px;border-radius:999px;text-decoration:none;color:var(--ink)}
.links a:hover{border-color:var(--accent);color:var(--accent)}
.stats{display:flex;gap:28px;margin:28px 0 0;flex-wrap:wrap}
.stat b{display:block;font-family:Georgia,serif;font-size:1.6rem;color:var(--ink)}
.stat span{color:var(--muted);font-size:.85rem}
ul.plain{padding-left:20px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 6px;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:2;border-bottom:1px solid var(--line)}
.toolbar input,.toolbar select{font:inherit;font-size:.95rem;padding:7px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.toolbar input{flex:1;min-width:220px}
.years{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 0;font-size:.85rem}
.years a{color:var(--muted);text-decoration:none;padding:1px 6px;border-radius:4px}
.years a:hover{background:var(--accent-soft);color:var(--accent)}
.pub{padding:12px 0;border-bottom:1px dashed var(--line)}
.pub .t{font-weight:600}
.pub .a{color:var(--ink);font-size:.93rem}
.pub .a .me{text-decoration:underline;text-decoration-color:var(--accent);text-underline-offset:3px}
.pub .v{color:var(--muted);font-size:.9rem;font-style:italic}
.pub .m{margin-top:4px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:.82rem}
.badge{font-size:.72rem;letter-spacing:.03em;text-transform:uppercase;padding:1px 7px;border-radius:3px;background:var(--accent-soft);color:var(--accent)}
.btn{font:inherit;font-size:.82rem;border:none;background:none;color:var(--accent);cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:2px}
pre.bib{display:none;background:#f3f1ec;padding:10px 12px;border-radius:6px;font-size:.78rem;overflow:auto;white-space:pre-wrap;margin:8px 0 0}
pre.bib.open{display:block}
.year-h{scroll-margin-top:70px}
.empty{color:var(--muted);display:none;padding:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin:0 0 12px}
.card .t{font-weight:600}
.card .meta{color:var(--muted);font-size:.9rem}
.pill{font-size:.75rem;padding:1px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted);margin-left:6px;white-space:nowrap}
.pill.on{border-color:#2f7a4f;color:#2f7a4f}
table.s{width:100%;border-collapse:collapse;font-size:.95rem}
table.s td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
table.s td:last-child{color:var(--muted);white-space:nowrap;text-align:right}
footer{border-top:1px solid var(--line);color:var(--muted);font-size:.82rem;padding:22px 0 40px}
.demo{background:var(--mark);border:1px solid #e9d68a;padding:10px 14px;border-radius:6px;font-size:.9rem;margin-bottom:24px}
@media (max-width:640px){.hero{grid-template-columns:1fr}.hero img,.ph{width:120px;height:144px}nav{gap:4px 14px}h1{font-size:1.6rem}}
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


def page(cfg, L, active, title, body, demo=False):
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
<main><div class="wrap">{banner}{body}</div></main>
<footer><div class="wrap">© {date.today().year} {esc(cfg['name'])} · {esc(cfg.get('affiliation',''))} · {L['updated']}: {date.today().isoformat()}</div></footer>
<script src="site.js"></script></body></html>"""


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
<div class="m"><span class="badge">{TYPE_LABELS[p['type']][1]}</span>{' '.join(links)}
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
    links = "".join(f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in cfg.get("links", []))
    links += f'<a href="mailto:{esc(cfg.get("email"))}">{esc(cfg.get("email"))}</a>' if cfg.get("email") else ""
    n_j = sum(1 for p in pubs if p["type"] == "journal")
    n_pub = sum(1 for p in pubs if p["type"] not in ("data", "preprint", "other"))
    n_data = sum(1 for p in pubs if p["type"] == "data")
    n_phd = sum(1 for s in students if s.get("level") == "phd")
    n_proj = len(projects)
    recent = "".join(render_pub(p, f"h{i}", me, L) for i, p in enumerate([q for q in pubs if q["type"] not in ("data", "preprint")][:5]))
    interests = "".join(f"<li>{esc(i)}</li>" for i in cfg.get("research_interests", []))
    home = f"""<section class="hero">{img}<div>
<h1>{esc(cfg.get('title',''))} {esc(cfg['name'])}</h1>
<p class="sub">{esc(cfg.get('position',''))}</p>
<p class="sub">{esc(cfg.get('unit',''))}<br>{esc(cfg.get('affiliation',''))}</p>
<div class="links">{links}</div>
<div class="stats"><div class="stat"><b>{n_pub}</b><span>{L['peer']}</span></div>
<div class="stat"><b>{n_j}</b><span>{TYPE_LABELS['journal'][0].lower()}</span></div>
<div class="stat"><b>{n_data}</b><span>{TYPE_LABELS['data'][0].lower()}</span></div>
<div class="stat"><b>{n_phd}</b><span>{L['phd'].lower()}</span></div>
<div class="stat"><b>{n_proj}</b><span>{L['projects'].lower()}</span></div></div>
</div></section>
<p style="margin-top:28px">{esc(cfg.get('bio',''))}</p>
<h2>{L['interests']}</h2><ul class="plain">{interests}</ul>
<h2>{L['pubs']} <a href="publications.html" style="font-size:.85rem;font-family:system-ui">→</a></h2>{recent}
<div class="empty"></div>"""
    (OUT / "index.html").write_text(page(cfg, L, "index.html", L["home"], home, demo), encoding="utf-8")

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
    pubs_html = f"""<h1>{L['pubs']}</h1><p class="sub">{len(pubs)} {L['total']} · {L['source']}</p>
<div class="years">{ylinks}</div>
<div class="toolbar"><input id="q" type="search" placeholder="{L['search']}"><select id="t">{opts}</select></div>
{''.join(blocks)}<div class="empty">{L['nores']}</div>"""
    (OUT / "publications.html").write_text(page(cfg, L, "publications.html", L["pubs"], pubs_html, demo), encoding="utf-8")

    # ---- studenci
    sh = [f"<h1>{L['students']}</h1>"]
    for lvl in ("phd", "msc", "beng", "intern"):
        group = [s for s in students if s.get("level") == lvl]
        if not group:
            continue
        group.sort(key=lambda s: (s.get("status") != "current", -(s.get("year") or 9999)))
        sh.append(f"<h2>{L[lvl]}</h2>")
        for s in group:
            st = s.get("status", "current")
            pill = f'<span class="pill{" on" if st=="current" else ""}">{L[st]}{(" " + str(s["year"])) if s.get("year") else ""}</span>'
            name = f'<a href="{esc(s["url"])}">{esc(s["name"])}</a>' if s.get("url") else esc(s["name"])
            sh.append(f'<div class="card"><div class="t">{name}{pill}</div>'
                      + (f'<div>{esc(s["topic"])}</div>' if s.get("topic") else "")
                      + (f'<div class="meta">{L["role"]}: {esc(s["role"])}</div>' if s.get("role") else "")
                      + "</div>")
    (OUT / "students.html").write_text(page(cfg, L, "students.html", L["students"], "".join(sh), demo), encoding="utf-8")

    # ---- projekty
    ph = [f"<h1>{L['projects']}</h1>"]
    for st, label in (("ongoing", L["ongoing"]), ("completed", L["done"])):
        group = [p for p in projects if p.get("status", "completed") == st]
        if not group:
            continue
        ph.append(f"<h2>{label}</h2>")
        for p in group:
            t = f'<a href="{esc(p["url"])}">{esc(p["title"])}</a>' if p.get("url") else esc(p["title"])
            meta = " · ".join(esc(x) for x in (p.get("funder"), p.get("years"),
                              f'{L["role"]}: {p["role"]}' if p.get("role") else None, p.get("partners")) if x)
            ph.append(f'<div class="card"><div class="t">{t}</div><div class="meta">{meta}</div>'
                      + (f'<p style="margin:6px 0 0">{esc(p["description"])}</p>' if p.get("description") else "")
                      + "</div>")
    (OUT / "projects.html").write_text(page(cfg, L, "projects.html", L["projects"], "".join(ph), demo), encoding="utf-8")

    # ---- działalność
    vh = [f"<h1>{L['service']}</h1>"]
    for g in service:
        vh.append(f"<h2>{esc(g['category'])}</h2><table class='s'>")
        for it in g.get("items", []):
            vh.append(f"<tr><td>{esc(it['text'])}</td><td>{esc(it.get('years',''))}</td></tr>")
        vh.append("</table>")
    (OUT / "service.html").write_text(page(cfg, L, "service.html", L["service"], "".join(vh), demo), encoding="utf-8")

    print(f"Gotowe: {len(pubs)} publikacji → {OUT}/")


if __name__ == "__main__":
    build(demo="--demo" in sys.argv, offline="--offline" in sys.argv)
