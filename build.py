#!/usr/bin/env python3
"""
Generator osobistej strony naukowej.

Źródła publikacji (łączone, bez duplikatów):
  1. ORCID public API  – pobierane automatycznie przy każdym budowaniu
                          (cache w data/orcid_cache.json, gdy API nie odpowiada)
  2. data/extra.bib    – opcjonalny eksport BibTeX z Bazy Wiedzy PW
  3. data/publications_list.yaml – pełny dorobek z wykazu osiągnięć (ma pierwszeństwo)
  4. data/publications_overrides.yaml – ręczne ukrywanie / dopisywanie

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


def bib_authors(bib):
    """Autorzy z pola author w BibTeX (także zapisanego w jednej linii)."""
    m = re.search(r"author\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", bib or "", re.I) or \
        re.search(r'author\s*=\s*"([^"]*)"', bib or "", re.I)
    if not m:
        return []
    out = []
    for a in re.split(r"\s+and\s+", m.group(1).replace("{", "").replace("}", "")):
        a = a.strip()
        if a:
            out.append(" ".join(reversed([x.strip() for x in a.split(",", 1)])) if "," in a else a)
    return out


def clean_authors(p):
    """Naprawa autorów w starszym cache (błędnie sparsowany BibTeX)."""
    if any(("=" in a or "{" in a or "}" in a) for a in p.get("authors", [])):
        p["authors"] = bib_authors(p.get("bibtex"))
    p["authors"] = [" ".join(reversed([x.strip() for x in a.split(",", 1)])) if a.count(",") == 1 else a
                    for a in p.get("authors", [])]
    return p


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
        authors = bib_authors(bib)
    return {
        "title": _v(w, "title", "title", "value") or "",
        "year": int(_v(w, "publication-date", "year", "value") or 0),
        "month": int(_v(w, "publication-date", "month", "value") or 0),
        # kiedy pozycja trafiła do ORCID – w obrębie roku najnowsze idą na górę
        "added": int(_v(w, "created-date", "value") or 0),
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


def _similar_lo(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.72


TALK_TYPES = ("conference-poster", "conference-abstract", "conference-presentation", "lecture-speech")


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
                for f in ("authors", "venue", "doi", "url", "bibtex", "year", "month", "added"):
                    if f == "bibtex" and hit.get("source") == "list":
                        continue  # dla pozycji z listy BibTeX generujemy z poprawnych danych
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
TYPE_LABELS_PL = {
    "journal": ("Artykuły w czasopismach", "Artykuł"),
    "conference": ("Referaty konferencyjne", "Konferencja"),
    "book": ("Książki", "Książka"),
    "chapter": ("Rozdziały w monografiach", "Rozdział"),
    "preprint": ("Preprinty", "Preprint"),
    "data": ("Zbiory danych i oprogramowanie", "Dane"),
    "other": ("Inne", "Inne"),
}

I18N = {
    "en": dict(home="Home", pubs="Publications", students="Students", projects="Projects",
               service="Service", forstud="For students", interests="Research interests", updated="Last updated",
               search="Search titles, authors, venues…", all="All types", phd="Doctoral candidates",
               msc="Master's theses", beng="Engineering theses", intern="Interns",
               current="current", completed="completed", ongoing="Ongoing", done="Completed",
               role="Role", total="publications", source="Publication list synchronised daily with ORCID.",
               cite="Cite", copy="Copy", copied="Copied!", close="Close", contact="Contact",
               nores="No matching publications.", peer="publications", about="About",
               recent="Recent publications", allp="all", datasets="datasets & software", journals="journal articles",
               home_kick="Profile", talks="Talks", coop="Cooperation", awards="Awards", highlights="Highlights",
               talks_sub="Invited and plenary lectures, conference presentations and posters",
               coop_sub="Industry cooperation, implemented results and academic partners",
               awards_sub="Awards, distinctions and recognition",
               talks_note="Conference presentations and posters are listed in the language in which they were given.",
               superv="Thesis supervision", sv_phd="doctoral (auxiliary supervisor)", sv_msc="master's theses",
               sv_beng="engineering theses", sv_all="theses supervised",
               stud_sub="Doctoral candidates and thesis students", proj_sub="Research and development projects",
               serv_sub="Editorial work, committees and professional activity",
               fs_sub="Thesis topics, what I offer and how to get in touch",
               topics="Open thesis topics", offer="What I offer", howto="How to apply", write="Write to me",
               lvl_any="BSc / MSc", lvl_phd="PhD", lvl_msc="MSc", lvl_beng="BSc",
               network="Collaboration network", coauthors="co-authors", coauth1="Co-author",
               net_hint="drag, zoom with the mouse wheel, click a person to see joint publications",
               joint="joint publications", all_pubs="publications",
               net_hint_touch="tap a person to highlight, tap again to see joint publications"),
    "pl": dict(home="Strona główna", pubs="Publikacje", students="Studenci", projects="Projekty",
               service="Działalność", forstud="Dla studentów", interests="Zainteresowania badawcze", updated="Aktualizacja",
               search="Szukaj w tytułach, autorach, czasopismach…", all="Wszystkie typy",
               phd="Doktoranci", msc="Prace magisterskie", beng="Prace inżynierskie", intern="Stażyści",
               current="w toku", completed="zakończone", ongoing="W realizacji", done="Zakończone",
               role="Rola", total="publikacji", source="Lista publikacji synchronizowana codziennie z ORCID.",
               cite="Cytuj", copy="Kopiuj", copied="Skopiowano!", close="Zamknij", contact="Kontakt",
               nores="Brak pasujących publikacji.", peer="publikacji", about="O mnie",
               recent="Najnowsze publikacje", allp="wszystkie", datasets="zbiorów danych i oprogramowania",
               journals="artykułów w czasopismach",
               home_kick="Profil", talks="Wystąpienia", coop="Współpraca", awards="Nagrody", highlights="Najważniejsze osiągnięcia",
               talks_sub="Wykłady na zaproszenie i plenarne, referaty konferencyjne i postery",
               coop_sub="Współpraca z przemysłem, wdrożenia i partnerzy naukowi",
               awards_sub="Nagrody, wyróżnienia i odznaczenia",
               talks_note="Referaty i postery podano w języku, w którym zostały wygłoszone.",
               superv="Promotorstwo prac dyplomowych", sv_phd="doktorat (promotor pomocniczy)", sv_msc="prac magisterskich",
               sv_beng="prac inżynierskich", sv_all="wypromowanych prac",
               stud_sub="Doktoranci i dyplomanci", proj_sub="Projekty badawcze i rozwojowe",
               serv_sub="Redakcje, komitety i działalność organizacyjna",
               fs_sub="Tematy prac dyplomowych, co oferuję i jak się zgłosić",
               topics="Proponowane tematy prac", offer="Co oferuję", howto="Jak się zgłosić", write="Napisz do mnie",
               lvl_any="inż. / mgr", lvl_phd="doktorat", lvl_msc="mgr", lvl_beng="inż.",
               network="Sieć współpracy", coauthors="współautorów", coauth1="Współautor",
               net_hint="przeciągaj, przybliżaj kółkiem myszy, kliknij osobę, aby zobaczyć wspólne publikacje",
               joint="wspólnych publikacji", all_pubs="publikacji",
               net_hint_touch="dotknij osoby, aby ją podświetlić, dotknij ponownie, aby zobaczyć wspólne publikacje"),
}
LEVEL_NAMES = {"en": {"phd": "Doctoral candidates", "ips": "Individual study programmes (IPS / ID)", "msc": "Master's theses",
                      "beng": "Engineering theses", "intern": "Interns"},
               "pl": {"phd": "Doktoranci", "ips": "Indywidualne programy studiów (IPS / ID)", "msc": "Prace magisterskie",
                      "beng": "Prace inżynierskie", "intern": "Stażyści"}}


def tr(obj, key, lang, default=""):
    """Wartość pola w danym języku: najpierw key_pl / key_en, potem key."""
    if not isinstance(obj, dict):
        return default
    v = obj.get(f"{key}_{lang}")
    return v if v not in (None, "") else obj.get(key, default)


def tlabels(lang):
    return TYPE_LABELS_PL if lang == "pl" else TYPE_LABELS


CSS = """
/* Styl bazowy; motyw „Ciemny Wykład” nadpisuje go niżej */
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
.stat:nth-child(4){border-top-color:var(--teal)}.stat:nth-child(5){border-top-color:var(--navy)}.stat:nth-child(6){border-top-color:var(--gold)}
h2 .cnt{font-family:var(--sans);font-size:.95rem;font-weight:700;color:var(--white);background:var(--primary);border-radius:20px;padding:2px 10px;margin-left:10px;vertical-align:middle}
ul.hl{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px 22px}
ul.hl li{position:relative;padding:4px 0 4px 22px;line-height:1.4}
ul.hl li:before{content:"";position:absolute;left:0;top:11px;width:10px;height:10px;background:var(--gold);transform:rotate(45deg)}
.note{color:var(--muted);font-size:.92rem;margin:18px 0 0}
.stats.inpage{margin:28px 0 8px}
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
.hero h1{font-size:1.9rem}nav{gap:4px 16px}header.site{position:static}.band.small h1{font-size:1.8rem}.toolbar{top:0;position:static}}
"""

CSS += """
/* --- animowana sieć w tle ciemnych pasów --- */
.band canvas.net{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
/* --- przełącznik języka --- */
nav a.lang{margin-left:6px;font-size:.8rem;font-weight:700;letter-spacing:.06em;color:var(--navy);background:var(--gold);
 padding:4px 10px;line-height:1.2;border-radius:3px;border-bottom:none;align-self:center}
nav a.lang:hover{background:var(--white);color:var(--navy)}
/* --- okno Cite --- */
.modal{position:fixed;inset:0;background:rgba(26,31,54,.62);display:none;align-items:center;justify-content:center;z-index:50;padding:20px}
.modal.open{display:flex}
.modal .box{background:var(--white);border-radius:8px;max-width:720px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.35);border-top:5px solid var(--gold);overflow:hidden}
.modal .hd{display:flex;align-items:center;justify-content:space-between;padding:16px 22px 0}
.modal .hd h3{margin:0;font-size:1.25rem}
.modal .x{font:inherit;font-size:1.6rem;line-height:1;border:none;background:none;color:var(--muted);cursor:pointer}
.tabs{display:flex;gap:4px;padding:14px 22px 0;border-bottom:1px solid var(--line)}
.tabs button{font:inherit;font-size:.92rem;font-weight:700;border:none;background:none;color:var(--muted);padding:8px 14px;cursor:pointer;border-bottom:3px solid transparent}
.tabs button.on{color:var(--primary);border-bottom-color:var(--accent)}
.modal pre{margin:0;padding:18px 22px;white-space:pre-wrap;font:15px/1.55 var(--sans);color:var(--text);min-height:90px;max-height:50vh;overflow:auto}
.modal pre.code{font:13px/1.5 ui-monospace,Consolas,monospace;background:var(--light)}
.modal .ft{display:flex;justify-content:flex-end;gap:10px;padding:0 22px 18px}
.cta{font:inherit;font-weight:700;font-size:.95rem;border:none;border-radius:4px;padding:10px 20px;cursor:pointer;background:var(--primary);color:var(--white);display:inline-block}
.cta:hover{background:var(--accent);color:var(--white)}
.cta.gold{background:var(--gold);color:var(--navy)}
.cta.gold:hover{background:var(--white)}
/* --- sieć współautorów --- */
.net-sub{color:var(--muted);margin:-8px 0 14px;font-size:.95rem}
.net-sub .touch{display:none}
@media (hover:none){.net-sub .mouse{display:none}.net-sub .touch{display:inline}}
.netbox{position:relative;background:var(--navy);border-radius:8px;box-shadow:var(--shadow);overflow:hidden;height:520px;border-top:4px solid var(--gold)}
#net{width:100%;height:100%;display:block;cursor:grab;touch-action:pan-y}
#net.dragging{cursor:grabbing}
#net line{stroke:rgba(24,160,214,.35)}
#net line.hl{stroke:rgba(232,168,56,.9)}
#net circle{stroke:rgba(255,255,255,.5);stroke-width:1;cursor:pointer;transition:opacity .2s}
#net text{fill:var(--ltext);font:12px var(--sans);pointer-events:none;transition:opacity .2s}
#net text.big{fill:var(--white);font-weight:700;font-size:13px}
#net .dim{opacity:.15}
.net-tip{position:absolute;pointer-events:none;background:var(--white);color:var(--navy);padding:6px 10px;border-radius:4px;font-size:.85rem;
 box-shadow:0 6px 18px rgba(0,0,0,.3);opacity:0;transition:opacity .15s;white-space:nowrap;border-left:3px solid var(--gold)}
.net-tip b{display:block}
.net-legend{position:absolute;left:14px;bottom:12px;display:flex;gap:14px;flex-wrap:wrap;font-size:.8rem;color:var(--ltext)}
.net-legend i{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--primary);margin-right:6px;vertical-align:-1px;border:1px solid rgba(255,255,255,.5)}
.net-legend i.me{background:var(--gold)}.net-legend i.st{background:var(--accent)}
.net-reset{position:absolute;right:12px;top:12px;font:inherit;font-size:1.1rem;width:34px;height:34px;border-radius:50%;border:1px solid rgba(203,213,225,.4);
 background:rgba(255,255,255,.06);color:var(--white);cursor:pointer}
.net-reset:hover{border-color:var(--gold);color:var(--gold)}
@media (max-width:720px){.netbox{height:420px}}
/* --- pojawianie się przy przewijaniu --- */
.rv{opacity:0;transform:translateY(22px);transition:opacity .7s ease,transform .7s cubic-bezier(.2,.7,.2,1)}
.rv.in{opacity:1;transform:none}
/* --- dla studentów --- */
.topic-card .lvl{display:inline-block;font-size:.7rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 9px;border-radius:3px;background:var(--accent);color:var(--white);margin-bottom:8px}
.topic-card .lvl.phd{background:var(--navy)}
.topic-card .kw{margin-top:10px;display:flex;flex-wrap:wrap;gap:6px}
.topic-card .kw span{font-size:.78rem;background:var(--light);color:var(--primary);padding:2px 8px;border-radius:3px}
.offer{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}
.offer div{background:var(--white);box-shadow:var(--shadow);border-radius:6px;padding:18px 20px;border-left:4px solid var(--gold)}
.offer b{display:block;font-family:var(--serif);color:var(--navy);font-size:1.05rem;margin-bottom:4px}
.contact-band{background:var(--navy);color:var(--ltext);border-radius:8px;padding:30px 32px;margin-top:48px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;border-left:6px solid var(--gold)}
.contact-band h3{color:var(--white);margin:0 0 6px;font-size:1.4rem}
.contact-band p{margin:0;max-width:560px}
"""

CSS += """
/* ================================================================
   MOTYW „CIEMNY WYKŁAD” – styl prezentacji konferencyjnych:
   głęboki granat z bursztynową poświatą, złote kropki i rozstrzelone
   etykiety, Georgia w zwykłej grubości, płaskie jasne karty.
   ================================================================ */
:root{--navy:#0F1B2D;--navy2:#1B355A;--primary:#2C5F7C;--accent:#3F7FA6;--gold:#E3A73B;--light:#F1F4F8;
--muted:#66758A;--text:#0F1B2D;--ltext:#A9B8CC;--line:#DCE3EC;--teal:#2C5F7C;--blue:#5A9AD6;
--shadow:none;--shadow-h:0 6px 20px rgba(15,27,45,.10)}
body{background:#fff}
h1,h2,h3{font-weight:400;letter-spacing:-.005em}
a:hover{color:var(--navy2)}
/* nagłówek */
header.site{background:var(--navy);border-top:0;box-shadow:none;border-bottom:1px solid rgba(169,184,204,.14)}
.brand{font-weight:400;font-size:1.15rem}
nav a{color:var(--ltext)}
/* etykieta-kropka (jak „● PROBLEM BADAWCZY” na slajdach) */
.kick{display:flex;align-items:center;gap:12px;font:700 .72rem/1.2 var(--sans);letter-spacing:.3em;text-transform:uppercase;color:var(--primary);margin:64px 0 10px}
.kick::before{content:"";flex:none;width:10px;height:10px;border-radius:50%;background:var(--gold)}
.kick+h2{margin-top:0}
.band .kick{color:var(--gold);margin:0 0 22px}
h2{font-size:1.85rem;margin:56px 0 22px;color:var(--navy)}
h2::after{display:none}
/* ciemne pasy jak slajd tytułowy */
.band{background:radial-gradient(ellipse 55% 70% at 88% 105%,rgba(227,167,59,.22),rgba(227,167,59,0) 70%),
 linear-gradient(120deg,#1B355A 0%,#13243C 45%,#0F1B2D 100%)}
.band::after{right:-160px;top:-210px;width:520px;height:520px;background:none;border:1.5px solid rgba(227,167,59,.45)}
.band::before{content:"";position:absolute;right:278px;top:228px;width:11px;height:11px;border-radius:50%;background:var(--gold);z-index:1;pointer-events:none}
.goldbar{display:none}
.band.small .wrap{padding-top:56px;padding-bottom:52px}
.band.small h1{font-size:2.9rem;font-weight:400}
.band.small p{color:var(--ltext);font-style:normal;font-size:1.12rem;margin-top:12px}
.hero .wrap{grid-template-columns:200px 1fr;gap:52px;padding-top:64px;padding-bottom:104px}
.hero img,.hero .ph{width:200px;height:242px;border:6px solid #fff;border-radius:8px;box-shadow:0 18px 40px rgba(0,0,0,.35)}
.hero h1{font-size:3.1rem;font-weight:400}
.hero .title{color:var(--ltext);font-style:normal;font-size:1.2rem;margin:10px 0 16px}
.hero .aff{color:var(--ltext)}
.hero .foot{margin-top:26px;font:700 .74rem var(--sans);letter-spacing:.3em;text-transform:uppercase;color:var(--gold)}
.links a{border-color:rgba(169,184,204,.35);background:transparent}
.links a:hover{border-color:var(--gold);color:var(--gold);background:transparent}
/* kafelki liczb */
.stat{background:#fff;border:1px solid var(--line);border-top:0;border-radius:8px;box-shadow:0 10px 30px rgba(15,27,45,.08);padding:20px 20px 16px;position:relative}
.stat::before{content:"";position:absolute;left:20px;top:0;width:28px;height:3px;background:var(--gold)}
.stat b{font-weight:400;font-size:2.35rem;color:var(--navy)}
.stats.inpage .stat{box-shadow:none;background:var(--light);border:0}
/* treść */
.lead{font-size:1.1rem;color:var(--text)}
.chips li{background:#fff;box-shadow:none;border:1.5px solid var(--gold);border-left:1.5px solid var(--gold);border-radius:999px;padding:7px 18px;color:var(--navy);font-weight:700;font-size:.93rem}
ul.hl li:before{width:9px;height:9px;border-radius:50%;transform:none;top:12px}
.more{color:var(--primary)}
h2 .cnt{background:var(--navy2)}
/* publikacje */
.toolbar{background:#fff}
.toolbar input,.toolbar select{box-shadow:none;background:var(--light);border-color:var(--line)}
.toolbar input:focus,.toolbar select:focus{outline-color:var(--gold)}
.years a{background:var(--light);box-shadow:none;color:var(--navy2)}
.years a:hover{background:var(--navy2);color:#fff}
.pub{background:var(--light);box-shadow:none;border-left:3px solid var(--navy2);border-radius:6px}
.pub:hover{box-shadow:var(--shadow-h);transform:none;background:#fff}
.pub[data-t=conference]{border-left-color:var(--primary)}.pub[data-t=data]{border-left-color:var(--blue)}
.pub .t{font-family:var(--serif);font-weight:400;font-size:1.1rem;color:var(--navy)}
.pub .a .me{color:var(--navy2)}
.badge{background:var(--navy2);letter-spacing:.14em;border-radius:3px}
.badge.conference{background:var(--primary)}.badge.data{background:var(--blue)}.badge.chapter,.badge.book{background:var(--gold);color:var(--navy)}
.btn:hover{color:var(--gold)}
/* karty: jasne, a bieżące – granatowe (jak „Czego nie widzą”) */
.card{background:var(--light);box-shadow:none;border-top:0;border-radius:8px;padding:22px 24px}
.card .t{font-weight:400;font-size:1.2rem}
.card:not(.done):not(.topic-card){background:var(--navy2);color:#fff}
.card:not(.done):not(.topic-card) .t,.card:not(.done):not(.topic-card) .t a{color:#fff}
.card:not(.done):not(.topic-card) .meta{color:var(--ltext)}
.pill{background:#fff;color:var(--muted);letter-spacing:.14em}
.pill.on{background:var(--gold);color:var(--navy)}
table.s{border-spacing:0 8px}
table.s td{background:var(--light);box-shadow:none}
table.s td:first-child{border-left:3px solid var(--gold)}
/* sieć, okno cytowania, stopka */
.netbox{background:radial-gradient(ellipse 50% 60% at 90% 100%,rgba(227,167,59,.16),rgba(227,167,59,0) 70%),linear-gradient(120deg,#1B355A,#0F1B2D);border-top:0;border-radius:10px}
#net line{stroke:rgba(169,184,204,.28)}
#net line.hl{stroke:rgba(227,167,59,.9)}
.net-legend i{background:var(--primary)}.net-legend i.st{background:var(--blue)}
.modal{background:rgba(15,27,45,.66)}
.modal .box{border-top:4px solid var(--gold)}
.tabs button.on{color:var(--navy2);border-bottom-color:var(--gold)}
.cta{background:var(--navy2)}.cta:hover{background:var(--navy)}
.offer div{background:var(--light);box-shadow:none;border-left:3px solid var(--gold)}
.offer b{font-weight:400}
.contact-band{background:linear-gradient(120deg,#1B355A,#0F1B2D);border-left:0}
.topic-card .lvl{background:var(--navy2)}
footer{background:var(--navy);border-top:0;color:var(--ltext)}
@media (max-width:720px){.band::before,.band::after{display:none}.hero .wrap{grid-template-columns:1fr;gap:26px;padding-top:40px;padding-bottom:96px}.kick{letter-spacing:.2em}.hero h1{font-size:2.2rem}.band.small h1{font-size:2.1rem}
.hero img,.hero .ph{width:150px;height:182px}.kick{margin-top:48px}}
"""

JS = """
/* ---- animowana sieć (węzły i krawędzie) w ciemnych pasach ---- */
(function(){
 const reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
 document.querySelectorAll('.band').forEach(band=>{
  const c=document.createElement('canvas');c.className='net';band.prepend(c);
  const ctx=c.getContext('2d');let W,H,pts=[],raf=null,visible=true;
  const dpr=Math.min(window.devicePixelRatio||1,2);
  function init(){
   W=band.clientWidth;H=band.clientHeight;c.width=W*dpr;c.height=H*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);
   const n=Math.min(80,Math.round(W*H/16000));pts=[];
   for(let i=0;i<n;i++)pts.push({x:Math.random()*W,y:Math.random()*H,vx:(Math.random()-.5)*.28,vy:(Math.random()-.5)*.28,
    r:Math.random()<.12?2.6:1.6,g:Math.random()<.12});
  }
  function draw(){
   ctx.clearRect(0,0,W,H);const D=130;
   for(let i=0;i<pts.length;i++){const a=pts[i];
    for(let j=i+1;j<pts.length;j++){const b=pts[j],dx=a.x-b.x,dy=a.y-b.y,d=Math.hypot(dx,dy);
     if(d<D){ctx.strokeStyle=`rgba(169,184,204,${(1-d/D)*.16})`;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}}}
   pts.forEach(p=>{ctx.fillStyle=p.g?'rgba(227,167,59,.75)':'rgba(169,184,204,.45)';ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,6.283);ctx.fill();});
  }
  function step(){pts.forEach(p=>{p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>W)p.vx*=-1;if(p.y<0||p.y>H)p.vy*=-1;});
   draw();raf=visible?requestAnimationFrame(step):null;}
  init();draw();
  if(reduce)return;
  if('IntersectionObserver' in window){new IntersectionObserver(es=>{visible=es[0].isIntersecting;if(visible&&!raf)raf=requestAnimationFrame(step);}).observe(band);}
  else raf=requestAnimationFrame(step);
  let t;window.addEventListener('resize',()=>{clearTimeout(t);t=setTimeout(()=>{init();draw();},150);});
 });
})();
/* ---- pojawianie się elementów przy przewijaniu ---- */
(function(){
 if(!('IntersectionObserver' in window))return;
 if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 const sel='main h2, main .lead, .chips, .pub, .card, .offer>div, table.s tr, .netbox, .contact-band, .years, .toolbar';
 const els=[...document.querySelectorAll(sel)].filter(el=>el.getBoundingClientRect().top>window.innerHeight*0.92);
 const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){
   const el=e.target,sib=[...el.parentNode.children].filter(x=>x.classList.contains('rv')&&!x.classList.contains('in'));
   el.style.transitionDelay=Math.min(sib.indexOf(el),5)*70+'ms';el.classList.add('in');io.unobserve(el);}}),{rootMargin:'0px 0px -8% 0px'});
 els.forEach(el=>{el.classList.add('rv');io.observe(el);});
})();
/* ---- sieć współautorów ---- */
(function(){
 const svg=document.getElementById('net'),dataEl=document.getElementById('netdata');if(!svg||!dataEl)return;
 const box=document.getElementById('netbox'),tip=document.getElementById('nettip'),NS='http://www.w3.org/2000/svg';
 const D=JSON.parse(dataEl.textContent),nodes=D.nodes,byId={};nodes.forEach(n=>byId[n.id]=n);
 const links=D.links.map(l=>({s:byId[l.s],t:byId[l.t],w:l.w,me:byId[l.s].me||byId[l.t].me}));
 let W=box.clientWidth,H=box.clientHeight;
 const me=nodes.find(n=>n.me);
 const maxN=Math.max(...nodes.filter(n=>!n.me).map(n=>n.n),1);
 nodes.forEach((n,i)=>{const a=i*2.399,r=60+Math.sqrt(i)*28;n.x=W/2+Math.cos(a)*r;n.y=H/2+Math.sin(a)*r;n.vx=0;n.vy=0;
  n.r=n.me?20:5+9*Math.sqrt(n.n/maxN);});
 me.x=W/2;me.y=H/2;me.fx=true;
 const g=document.createElementNS(NS,'g');svg.appendChild(g);
 const nb={};links.forEach(l=>{(nb[l.s.id]=nb[l.s.id]||new Set()).add(l.t.id);(nb[l.t.id]=nb[l.t.id]||new Set()).add(l.s.id);});
 links.forEach(l=>{l.el=document.createElementNS(NS,'line');l.el.setAttribute('stroke-width',l.me?Math.min(1+l.w*.7,6):Math.min(.6+l.w*.4,3));g.appendChild(l.el);});
 nodes.forEach(n=>{
  n.c=document.createElementNS(NS,'circle');n.c.setAttribute('r',n.r);
  n.c.setAttribute('fill',n.me?'#E3A73B':n.st?'#5A9AD6':'#2C5F7C');g.appendChild(n.c);
  n.t=document.createElementNS(NS,'text');n.t.textContent=n.me?'':n.name;
  const minN=W<600?3:2;
  if(n.n>=minN||n.st)n.t.classList.add('big');else n.t.style.opacity=0;
  g.appendChild(n.t);
 });
 let alpha=1,raf=null;
 function tick(){
  const k=alpha;
  for(let i=0;i<nodes.length;i++){const a=nodes[i];
   for(let j=i+1;j<nodes.length;j++){const b=nodes[j];let dx=b.x-a.x,dy=b.y-a.y,d2=dx*dx+dy*dy||1,d=Math.sqrt(d2);
    const f=Math.min(1800/d2,4)*k,fx=dx/d*f,fy=dy/d*f;a.vx-=fx;a.vy-=fy;b.vx+=fx;b.vy+=fy;
    const md=a.r+b.r+6;if(d<md){const p=(md-d)/d*.5;a.vx-=dx*p*.5;a.vy-=dy*p*.5;b.vx+=dx*p*.5;b.vy+=dy*p*.5;}}}
  links.forEach(l=>{const dx=l.t.x-l.s.x,dy=l.t.y-l.s.y,d=Math.sqrt(dx*dx+dy*dy)||1;
   const target=l.me?150-Math.min(l.w,8)*9:90,f=(d-target)/d*(l.me?.06:.02)*k;
   l.s.vx+=dx*f;l.s.vy+=dy*f;l.t.vx-=dx*f;l.t.vy-=dy*f;});
  nodes.forEach(n=>{n.vx+=(W/2-n.x)*.004*k;n.vy+=(H/2-n.y)*.004*k;
   if(n.fx||n.drag){n.vx=n.vy=0;return;}n.vx*=.6;n.vy*=.6;n.x+=n.vx;n.y+=n.vy;});
  draw();alpha*=.985;
  if(!userView)fit();
  raf=alpha>.02?requestAnimationFrame(tick):null;
 }
 let userView=false;
 function fit(){   // dopasuj cały graf do okna (ważne na telefonie)
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  nodes.forEach(n=>{x0=Math.min(x0,n.x-n.r);y0=Math.min(y0,n.y-n.r);x1=Math.max(x1,n.x+n.r+(n.t.style.opacity==='0'?0:70));y1=Math.max(y1,n.y+n.r);});
  const pad=24,bw=x1-x0+2*pad,bh=y1-y0+2*pad+30;
  sc=Math.min(1,W/bw,H/bh);tx=(W-(x0+x1)*sc)/2;ty=(H-30-(y0+y1)*sc)/2;apply();
 }
 function draw(){
  links.forEach(l=>{l.el.setAttribute('x1',l.s.x);l.el.setAttribute('y1',l.s.y);l.el.setAttribute('x2',l.t.x);l.el.setAttribute('y2',l.t.y);});
  nodes.forEach(n=>{n.c.setAttribute('cx',n.x);n.c.setAttribute('cy',n.y);n.t.setAttribute('x',n.x+n.r+4);n.t.setAttribute('y',n.y+4);});
 }
 function heat(a){alpha=Math.max(alpha,a);if(!raf)raf=requestAnimationFrame(tick);}
 // zoom / pan
 let sc=1,tx=0,ty=0;const apply=()=>g.setAttribute('transform',`translate(${tx},${ty}) scale(${sc})`);
 function pt(e){const r=svg.getBoundingClientRect();return{x:(e.clientX-r.left-tx)/sc,y:(e.clientY-r.top-ty)/sc,sx:e.clientX-r.left,sy:e.clientY-r.top};}
 svg.addEventListener('wheel',e=>{e.preventDefault();userView=true;const p=pt(e),f=e.deltaY<0?1.12:1/1.12;const ns=Math.min(4,Math.max(.4,sc*f));
  tx=p.sx-p.x*ns;ty=p.sy-p.y*ns;sc=ns;apply();},{passive:false});
 let drag=null,pan=null,moved=false;
 let focused=null;
 svg.addEventListener('pointerdown',e=>{moved=false;const n=nodes.find(n=>n.c===e.target);
  if(e.pointerType==='touch'){if(n){drag=n;}return;}   // dotyk: tło przewija stronę
  svg.setPointerCapture(e.pointerId);svg.classList.add('dragging');
  if(n){drag=n;n.drag=true;}else{const p=pt(e);pan={x:p.sx-tx,y:p.sy-ty};}});
 svg.addEventListener('pointercancel',()=>{if(drag)drag.drag=false;drag=null;pan=null;svg.classList.remove('dragging');});
 svg.addEventListener('pointermove',e=>{const p=pt(e);
  if(e.pointerType==='touch')return;
  if(drag){moved=true;drag.x=p.x;drag.y=p.y;heat(.3);draw();}
  else if(pan){moved=true;userView=true;tx=p.sx-pan.x;ty=p.sy-pan.y;apply();}});
 svg.addEventListener('pointerup',e=>{svg.classList.remove('dragging');
  if(drag){drag.drag=false;
   if(!moved){
    if(e.pointerType==='touch'&&focused!==drag){focused=drag;focus(drag);placeTip(drag);}
    else if(!drag.me){const last=drag.name.split(' ').pop();location.href='publications.html#q='+encodeURIComponent(last);}
   }}
  else if(e.pointerType==='touch'){focused=null;blur();}
  drag=null;pan=null;});
 // podświetlenie i dymek
 function focus(n){
  const ns=nb[n.id]||new Set();
  nodes.forEach(m=>{const on=m===n||ns.has(m.id);m.c.classList.toggle('dim',!on);m.t.classList.toggle('dim',!on);if(on&&m!==n&&!m.me)m.t.style.opacity=1;});
  links.forEach(l=>{const on=l.s===n||l.t===n;l.el.classList.toggle('hl',on);l.el.classList.toggle('dim',!on);});
  tip.textContent='';const bb=document.createElement('b');bb.textContent=n.name;tip.append(bb,n.n+' '+(n.me?box.dataset.all:box.dataset.joint));tip.style.opacity=1;
 }
 function blur(){nodes.forEach(m=>{m.c.classList.remove('dim');m.t.classList.remove('dim');if(!m.t.classList.contains('big'))m.t.style.opacity=0;});
  links.forEach(l=>l.el.classList.remove('hl','dim'));tip.style.opacity=0;}
 nodes.forEach(n=>{n.c.addEventListener('pointerenter',e=>{if(e.pointerType!=='touch')focus(n);});
  n.c.addEventListener('pointerleave',e=>{if(e.pointerType!=='touch')blur();});});
 function placeTip(n){const x=n.x*sc+tx,y=n.y*sc+ty;let lx=x+n.r+10;if(lx>box.clientWidth-210)lx=x-220;
  tip.style.left=Math.max(6,lx)+'px';tip.style.top=Math.max(6,y-20)+'px';}
 svg.addEventListener('pointermove',e=>{if(e.pointerType==='touch')return;const r=box.getBoundingClientRect();let x=e.clientX-r.left+14,y=e.clientY-r.top+14;
  if(x>r.width-200)x-=220;tip.style.left=x+'px';tip.style.top=y+'px';});
 box.querySelector('.net-reset').addEventListener('click',()=>{userView=false;heat(.6);});
 window.addEventListener('resize',()=>{W=box.clientWidth;H=box.clientHeight;me.x=W/2;me.y=H/2;heat(.3);});
 // startuj symulację, gdy sieć pojawi się na ekranie
 draw();
 if('IntersectionObserver' in window){const io=new IntersectionObserver(es=>{if(es[0].isIntersecting){heat(1);io.disconnect();}});io.observe(box);}else heat(1);
})();
/* ---- animacja liczników ---- */
(function(){
 const els=[...document.querySelectorAll('[data-count]')];
 if(!els.length)return;
 if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 const ease=t=>1-Math.pow(1-t,3);
 function run(el,i){
  const target=+el.dataset.count,dur=1400+i*150;let t0=null;
  function step(ts){if(!t0)t0=ts;const k=Math.min((ts-t0)/dur,1);
   el.textContent=Math.round(target*ease(k));if(k<1)requestAnimationFrame(step);}
  requestAnimationFrame(step);
 }
 els.forEach(el=>el.textContent='0');
 if('IntersectionObserver' in window){
  const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){run(e.target,els.indexOf(e.target));io.unobserve(e.target);}}),{threshold:.4});
  els.forEach(el=>io.observe(el));
 } else els.forEach(run);
})();
/* ---- wyszukiwarka publikacji ---- */
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
 const m=location.hash.match(/^#q=(.+)$/);if(m){q.value=decodeURIComponent(m[1]);run();window.scrollTo(0,0);}
})();
/* ---- okno Cite (APA / IEEE / BibTeX) ---- */
(function(){
 const m=document.getElementById('cite');if(!m)return;
 const pre=m.querySelector('pre'),tabs=[...m.querySelectorAll('.tabs button')],copy=m.querySelector('.copy');
 let cur=null,fmt='apa';
 function show(){pre.textContent=cur?cur[fmt]:'';pre.classList.toggle('code',fmt==='bib');tabs.forEach(b=>b.classList.toggle('on',b.dataset.f===fmt));}
 function open(i){const el=document.getElementById('c'+i);if(!el)return;cur=JSON.parse(el.textContent);m.classList.add('open');show();}
 function close(){m.classList.remove('open');}
 document.addEventListener('click',e=>{
  const b=e.target.closest('[data-cite]');if(b){open(b.dataset.cite);return;}
  const tb=e.target.closest('.tabs button');if(tb){fmt=tb.dataset.f;show();return;}
  if(e.target===m||e.target.closest('.x'))close();
 });
 document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
 copy.addEventListener('click',()=>{
  const txt=pre.textContent,o=copy.textContent;
  const done=()=>{copy.textContent=copy.dataset.ok;setTimeout(()=>copy.textContent=o,1300);};
  if(navigator.clipboard)navigator.clipboard.writeText(txt).then(done).catch(()=>{});
 });
})();
"""


# ----------------------------------------------------------------- cytowania
def _split(name):
    name = name.strip()
    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
    else:
        parts = name.split()
        last, first = parts[-1], " ".join(parts[:-1])
    initials = " ".join(f"{w[0]}." for w in re.split(r"[\s\-]+", first) if w and w[0].isalpha())
    return last, initials


def cite_apa(p):
    names = [f"{l}, {i}".strip(", ") for l, i in map(_split, p["authors"])]
    if len(names) > 20:
        names = names[:19] + ["… " + names[-1]]
    if len(names) <= 1:
        au = "".join(names)
    elif len(names) == 2:
        au = f"{names[0]}, & {names[1]}"
    else:
        au = ", ".join(names[:-1]) + f", & {names[-1]}"
    s = f"{au} ({p['year'] or 'n.d.'}). {p['title'].rstrip('.')}."
    if p.get("venue"):
        s += f" {p['venue']}."
    if p.get("doi"):
        s += f" https://doi.org/{p['doi']}"
    return s.strip()


def cite_ieee(p):
    names = [f"{i} {l}".strip() for l, i in map(_split, p["authors"])]
    if len(names) > 6:
        au = names[0] + " et al."
    elif len(names) <= 2:
        au = " and ".join(names)
    else:
        au = ", ".join(names[:-1]) + ", and " + names[-1]
    s = f"{au}, “{p['title'].rstrip('.')},”"
    if p.get("venue"):
        s += f" {p['venue']},"
    s += f" {p['year']}" if p.get("year") else ""
    s += f", doi: {p['doi']}." if p.get("doi") else "."
    return s.strip()


# ----------------------------------------------------------------- obraz do podglądu w social mediach
def make_og_image(cfg, path):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    import random
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), (26, 31, 54))
    d = ImageDraw.Draw(img, "RGBA")
    rnd = random.Random(7)
    pts = [(rnd.randint(560, 1200), rnd.randint(0, 630)) for _ in range(46)]
    for i, a in enumerate(pts):
        for b in pts[i + 1:]:
            dist = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** .5
            if dist < 150:
                d.line([a, b], fill=(24, 160, 214, int(90 * (1 - dist / 150))), width=1)
    for k, (x, y) in enumerate(pts):
        r = 4 if k % 8 == 0 else 2.5
        d.ellipse([x - r, y - r, x + r, y + r], fill=(232, 168, 56, 230) if k % 8 == 0 else (24, 160, 214, 200))
    d.rectangle([0, 0, W, 8], fill=(24, 160, 214))
    d.rectangle([0, H - 8, W, H], fill=(232, 168, 56))

    def font(names, size):
        for n in names:
            for base in ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/crosextra/",
                         "/usr/share/fonts/truetype/liberation/", ""):
                try:
                    return ImageFont.truetype(base + n, size)
                except OSError:
                    pass
        return ImageFont.load_default()
    serif = font(["DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"], 62)
    sans = font(["Carlito-Regular.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"], 30)
    sans_i = font(["Carlito-Italic.ttf", "LiberationSans-Italic.ttf", "DejaVuSans-Oblique.ttf"], 32)
    x = 80
    photo = cfg.get("photo")
    if photo and (ROOT / photo).exists():
        ph = Image.open(ROOT / photo).convert("RGB")
        ph_w, ph_h = 230, 280
        sc = max(ph_w / ph.width, ph_h / ph.height)
        ph = ph.resize((int(ph.width * sc) + 1, int(ph.height * sc) + 1))
        ph = ph.crop(((ph.width - ph_w) // 2, (ph.height - ph_h) // 2, (ph.width - ph_w) // 2 + ph_w, (ph.height - ph_h) // 2 + ph_h))
        d.rectangle([x - 5, 170, x + ph_w + 5, 170 + ph_h + 10], fill=(232, 168, 56))
        img.paste(ph, (x, 175))
        x += ph_w + 60
    d.rectangle([x, 175, x + 90, 181], fill=(232, 168, 56))
    words, lines, cur = cfg["name"].split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=serif) > W - x - 60 and cur:
            lines.append(cur); cur = w
        else:
            cur = test
    lines.append(cur)
    y = 205
    for ln in lines:
        d.text((x, y), ln, font=serif, fill=(255, 255, 255)); y += 74
    d.text((x, y + 8), cfg.get("title", ""), font=sans_i, fill=(24, 160, 214)); y += 60
    for ln in (cfg.get("affiliation", ""),):
        d.text((x, y), ln, font=sans, fill=(203, 213, 225)); y += 40
    kw = ""
    for it in cfg.get("research_interests", []):
        test = (kw + " · " + it) if kw else it
        if d.textlength(test, font=sans) > W - 160:
            break
        kw = test
    d.text((80, H - 80), kw, font=sans, fill=(232, 168, 56))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return True


import hashlib
ASSET_V = ""  # ustawiane w build() – skrót treści CSS/JS, wymusza pobranie nowej wersji przez przeglądarki


# ----------------------------------------------------------------- sieć współautorów
def person_key(name):
    last, initials = _split(name)
    k = f"{last}|{initials[:1]}".translate(str.maketrans("ŁłØøĐđ", "LlOoDd"))
    k = unicodedata.normalize("NFKD", k).encode("ascii", "ignore").decode().lower()
    return k


def coauthor_graph(pubs, me_patterns, students):
    from itertools import combinations
    names, count, pairs = {}, {}, {}
    student_keys = {person_key(re.sub(r",.*$", "", s.get("name", ""))) for s in students}
    me_key = None
    for p in pubs:
        keys = []
        for a in p.get("authors", []):
            k = person_key(a)
            if any(re.search(pat, a, re.I) for pat in me_patterns):
                me_key = me_key or k
                k = me_key
            disp = " ".join(reversed([x.strip() for x in a.split(",", 1)])) if "," in a else a
            if len(disp) > len(names.get(k, "")):
                names[k] = disp
            if k not in keys:
                keys.append(k)
        for k in keys:
            count[k] = count.get(k, 0) + 1
        for a, b in combinations(sorted(keys), 2):
            pairs[(a, b)] = pairs.get((a, b), 0) + 1
    if not me_key:
        return None
    nodes = [{"id": k, "name": names[k], "n": count[k] if k == me_key else pairs.get(tuple(sorted((k, me_key))), 0),
              "me": k == me_key, "st": k in student_keys}
             for k in count if k == me_key or pairs.get(tuple(sorted((k, me_key))), 0)]
    ids = {n["id"] for n in nodes}
    links = [{"s": a, "t": b, "w": w} for (a, b), w in pairs.items() if a in ids and b in ids]
    return {"nodes": nodes, "links": links}


# ----------------------------------------------------------------- szablon strony
PAGES = ["index.html", "publications.html", "projects.html", "cooperation.html", "talks.html", "students.html",
         "service.html", "awards.html", "for-students.html"]


def page(ctx, active, title, body, band=""):
    cfg, L, lang, base = ctx["cfg"], ctx["L"], ctx["lang"], ctx["base"]
    tabs = [("index.html", L["home"]), ("publications.html", L["pubs"]), ("projects.html", L["projects"]),
            ("cooperation.html", L["coop"]), ("talks.html", L["talks"]), ("students.html", L["students"]),
            ("service.html", L["service"]), ("awards.html", L["awards"])]
    if ctx["fs_show"]:
        tabs.append(("for-students.html", L["forstud"]))
    nav = "".join(f'<a href="{h}"{" class=active" if h == active else ""}>{esc(n)}</a>' for h, n in tabs)
    other = ctx["other_lang"]
    nav += f'<a class="lang" href="{ctx["other_prefix"]}{active}" hreflang="{other}" lang="{other}">{other.upper()}</a>'
    site = (cfg.get("site_url") or "").rstrip("/") + "/"
    url = site + ctx["prefix"] + ("" if active == "index.html" else active)
    desc = tr(cfg, "og_description", lang) or f"{cfg['name']} – {tr(cfg, 'affiliation', lang)}"
    gc = cfg.get("goatcounter")  # statystyki odwiedzin (GoatCounter, bez ciasteczek)
    # Polityka bezpieczeństwa treści: strona może ładować skrypty tylko z siebie i z licznika GoatCounter
    gch = f" https://{gc}.goatcounter.com" if gc else ""
    csp = ("default-src 'self'; script-src 'self'" + (" https://gc.zgo.at" if gc else "") +
           "; style-src 'self' 'unsafe-inline'; img-src 'self' data:" + gch + "; connect-src 'self'" + gch +
           "; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'; upgrade-insecure-requests")
    gc_html = (f'<script data-goatcounter="https://{esc(gc)}.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>\n'
               if gc else "")
    og_file = "og-pl.png" if lang == "pl" and (ROOT / "assets" / "og-pl.png").exists() else "og.png"
    og_p = ROOT / "assets" / og_file
    og_img = f"{site}assets/{og_file}" + (f"?v={hashlib.md5(og_p.read_bytes()).hexdigest()[:8]}" if og_p.exists() else "")
    og = f"""<meta property="og:type" content="website">
<meta property="og:title" content="{esc(cfg['name'])} – {esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{esc(og_img)}">
<meta property="og:image:alt" content="{esc(cfg['name'])}">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:locale" content="{'pl_PL' if lang == 'pl' else 'en_GB'}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{esc(og_img)}">
<link rel="canonical" href="{esc(url)}">
<link rel="alternate" hreflang="{lang}" href="{esc(url)}">
<link rel="alternate" hreflang="{other}" href="{esc(site + ctx['other_site_prefix'] + ('' if active == 'index.html' else active))}">
<meta http-equiv="Content-Security-Policy" content="{esc(csp)}">
<meta name="referrer" content="strict-origin-when-cross-origin">
<link rel="icon" type="image/png" sizes="32x32" href="{esc(site)}assets/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="{esc(site)}assets/favicon-16.png">
<link rel="icon" type="image/png" sizes="192x192" href="{esc(site)}assets/favicon-192.png">
<link rel="apple-touch-icon" href="{esc(site)}assets/apple-touch-icon.png">
<meta name="theme-color" content="#0F1B2D">"""
    cite_modal = ""
    if "data-cite" in body:
        cite_modal = f"""<div class="modal" id="cite" role="dialog" aria-modal="true"><div class="box">
<div class="hd"><h3>{L['cite']}</h3><button class="x" aria-label="{L['close']}">×</button></div>
<div class="tabs"><button data-f="apa" class="on">APA</button><button data-f="ieee">IEEE</button><button data-f="bib">BibTeX</button></div>
<pre></pre><div class="ft"><button class="cta copy" data-ok="{L['copied']}">{L['copy']}</button></div></div></div>"""
    # etykiety-kropki nad nagłówkami sekcji, jak na slajdach („● WYSTĄPIENIA 2 / 6”)
    n_h2 = body.count("<h2>")
    kname = L["home_kick"] if active == "index.html" else title
    cnt = iter(range(1, n_h2 + 1))
    body = re.sub(r"<h2>", lambda m: f'<div class="kick">{esc(kname)} &nbsp;{next(cnt)} / {n_h2}</div><h2>', body)
    band = band.replace('<div class="goldbar"></div>',
                        f'<div class="kick">{esc(tr(cfg, "kicker", lang) if "hero" in band else cfg["name"])}</div>', 1)
    return f"""<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} – {esc(cfg['name'])}</title>
<meta name="description" content="{esc(desc)}">
{og}
<link rel="stylesheet" href="{base}style.css?v={ASSET_V}">{gc_html}</head><body>
<header class="site"><div class="wrap"><a class="brand" href="index.html">{esc(cfg['name'])}</a><nav>{nav}</nav></div></header>
{band}
<main><div class="wrap">{body}</div></main>
<footer><div class="wrap"><span>© {date.today().year} {esc(cfg['name'])} · {esc(tr(cfg, 'affiliation', lang))}</span><span>{L['updated']}: {date.today().isoformat()}</span></div></footer>
{cite_modal}
<script src="{base}site.js?v={ASSET_V}"></script></body></html>"""


def small_band(title, sub=""):
    return (f'<section class="band small"><div class="wrap"><div class="goldbar"></div><h1>{esc(title)}</h1>'
            + (f'<p>{esc(sub)}</p>' if sub else "") + '</div></section>')


def sections_html(groups, lang):
    """Grupy {category, items:[{text, years}]} jako tabele – Działalność, Wystąpienia, Współpraca, Nagrody."""
    out = []
    for g in groups or []:
        items = g.get("items", []) or []
        cnt = f' <span class="cnt">{len(items)}</span>' if g.get("count", True) and items else ""
        out.append(f"<h2>{esc(tr(g, 'category', lang))}{cnt}</h2><table class='s'>")
        for it in items:
            out.append(f"<tr><td>{esc(tr(it, 'text', lang))}</td><td>{esc(str(it.get('years', '')))}</td></tr>")
        out.append("</table>")
    return "".join(out)


def stats_html(items, cls=""):
    return (f'<div class="stats{cls}">' + "".join(
        f'<div class="stat"><b data-count="{v}">{v}</b><span>{esc(lbl)}</span></div>' for v, lbl in items) + "</div>")


def fmt_authors(authors, me_patterns):
    out = []
    for a in authors:
        s = esc(a)
        if any(re.search(pat, a, re.I) for pat in me_patterns):
            s = f'<span class="me">{s}</span>'
        out.append(s)
    return ", ".join(out)


def render_pub(p, idx, me, L, TL):
    links = []
    if p.get("doi"):
        links.append(f'<a href="https://doi.org/{esc(p["doi"])}">DOI</a>')
    elif p.get("url"):
        links.append(f'<a href="{esc(p["url"])}">Link</a>')
    search = " ".join([p["title"], " ".join(p["authors"]), p.get("venue", ""), str(p["year"])]).lower()
    cdata = json.dumps({"apa": cite_apa(p), "ieee": cite_ieee(p), "bib": make_bibtex(p)}, ensure_ascii=False).replace("</", "<\\/")
    return f"""<div class="pub" data-y="{p['year']}" data-t="{p['type']}" data-s="{esc(search)}">
<div class="t">{esc(p['title'])}</div>
<div class="a">{fmt_authors(p['authors'], me)}</div>
{f'<div class="v">{esc(p["venue"])}</div>' if p.get('venue') else ''}
<div class="m"><span class="badge {p['type']}">{TL[p['type']][1]}</span>{' '.join(links)}
<button class="btn" data-cite="{idx}">“” {L['cite']}</button></div>
<script type="application/json" id="c{idx}">{cdata}</script></div>"""


# ----------------------------------------------------------------- budowanie
def collect(demo, offline, cfg):
    overrides = load_yaml("data/publications_overrides.yaml", {}) or {}
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
    orcid_pubs = [clean_authors(p) for p in orcid_pubs]
    # wystąpienia (postery, abstrakty, referaty bez publikacji) są w zakładce „Wystąpienia”, nie w publikacjach
    orcid_pubs = [p for p in orcid_pubs if p.get("orcid_type") not in TALK_TYPES
                  and not (p.get("orcid_type") == "conference-paper" and not p.get("venue") and not p.get("doi"))]
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
    list_pubs = []
    for m in load_yaml("data/publications_list.yaml", []) or []:
        m = dict(m, source="list", doi=norm_doi(m.get("doi")), url=m.get("url", ""))
        if isinstance(m.get("authors"), str):
            m["authors"] = [a.strip() for a in m["authors"].split(",")]
        m.setdefault("authors", []); m.setdefault("venue", ""); m["year"] = int(m.get("year") or 0)
        list_pubs.append(m)
    # lista z wykazu pierwsza – jej tytuły/czasopisma wygrywają; ORCID dopisuje braki
    pubs = apply_overrides(merge(list_pubs, orcid_pubs, bib_pubs), overrides)
    # preprint, który ma już wersję opublikowaną w czasopiśmie, nie jest pokazywany osobno
    publ = [(norm_title(q["title"]), q["year"]) for q in pubs if q["type"] in ("journal", "conference", "chapter")]
    pubs = [p for p in pubs if p["type"] != "preprint" or not any(
        abs(y - p["year"]) <= 1 and _similar_lo(t, norm_title(p["title"])) for t, y in publ)]
    # rok malejąco, w roku: najpóźniej dodane do ORCID na górze (nowa publikacja = pierwsza na liście)
    pubs.sort(key=lambda p: (-(p.get("year") or 0), -(p.get("added") or 0), -(p.get("month") or 0),
                             TYPE_RANK.index(p["type"]), p["title"].lower()))
    return pubs, auto_links


def build_lang(ctx, pubs, auto_links, data, outdir):
    cfg, L, lang = ctx["cfg"], ctx["L"], ctx["lang"]
    TL = tlabels(lang)
    students, projects, service, fs = data["students"], data["projects"], data["service"], data["fs"]
    me = cfg.get("highlight_name_patterns") or [r"Borkowski"]
    outdir.mkdir(parents=True, exist_ok=True)
    W = lambda name, html_: (outdir / name).write_text(html_, encoding="utf-8")

    # ---- strona główna
    photo = cfg.get("photo")
    if photo and (ROOT / photo).exists():
        img = f'<img src="{ctx["base"]}{esc(photo)}" alt="{esc(cfg["name"])}">'
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
    featured = [q for q in pubs if q["type"] not in ("data", "preprint")][:5]
    recent = "".join(render_pub(p, f"h{i}", me, L, TL) for i, p in enumerate(featured))
    interests = "".join(f"<li>{esc(i)}</li>" for i in tr(cfg, "research_interests", lang, []))
    g = coauthor_graph(pubs, me, students)
    net_html = ""
    if g and len(g["nodes"]) > 3:
        gj = json.dumps(g, ensure_ascii=False).replace("</", "<\\/")
        n_co = len(g["nodes"]) - 1
        net_html = f"""<h2>{L['network']}</h2>
<p class="net-sub">{n_co} {L['coauthors']} · <span class="mouse">{L['net_hint']}</span><span class="touch">{L['net_hint_touch']}</span></p>
<div class="netbox" id="netbox" data-joint="{L['joint']}" data-all="{L['all_pubs']}"><svg id="net" role="img" aria-label="{L['network']}"></svg>
<div class="net-tip" id="nettip"></div>
<div class="net-legend"><span><i class="me"></i>{esc(cfg['name'])}</span><span><i class="st"></i>{L['phd']}</span><span><i></i>{L['coauth1']}</span></div>
<button class="net-reset" type="button">⟲</button></div>
<script type="application/json" id="netdata">{gj}</script>"""
    hero = f"""<section class="band hero"><div class="wrap">{img}<div>
<div class="goldbar"></div>
<h1>{esc(cfg['name'])}</h1>
<p class="title">{esc(tr(cfg, 'title', lang))}</p>
<p class="aff">{esc(tr(cfg, 'unit', lang))}<br>{esc(tr(cfg, 'affiliation', lang))}</p>
<div class="links">{links}</div>
{f'<div class="foot">{esc(tr(cfg, "hero_foot", lang))}</div>' if tr(cfg, "hero_foot", lang) else ""}
</div></div></section>"""
    auto = {"pubs": (n_pub, L["peer"]), "journals": (n_j, L["journals"]), "data": (n_data, L["datasets"]),
            "phd": (n_phd, L["phd"].lower()), "projects": (n_proj, L["projects"].lower())}
    st = []
    for s_ in cfg.get("stats") or [{"auto": k} for k in auto]:
        if s_.get("auto") in auto:
            v, lbl = auto[s_["auto"]]
            st.append((v, tr(s_, "label", lang) or lbl))
        else:
            st.append((s_.get("value"), tr(s_, "label", lang)))
    hl = [tr(h, "text", lang) for h in cfg.get("highlights") or []]
    hl_html = (f"<h2>{L['highlights']}</h2><ul class='hl'>" + "".join(f"<li>{esc(x)}</li>" for x in hl) + "</ul>") if hl else ""
    home = stats_html(st) + f"""
<h2>{L['about']}</h2><p class="lead">{esc(tr(cfg, 'bio', lang))}</p>
{hl_html}<h2>{L['interests']}</h2><ul class="chips">{interests}</ul>
{net_html}
<h2>{L['recent']}<a class="more" href="publications.html">→ {L['allp']}</a></h2>{recent}
<div class="empty"></div>"""
    W("index.html", page(ctx, "index.html", L["home"], home, hero))

    # ---- publikacje
    years = sorted({p["year"] for p in pubs}, reverse=True)
    types_present = [k for k in TL if any(p["type"] == k for p in pubs)]
    opts = f'<option value="">{L["all"]}</option>' + "".join(
        f'<option value="{k}">{TL[k][0]} ({sum(1 for p in pubs if p["type"]==k)})</option>' for k in types_present)
    ylinks = "".join(f'<a href="#y{y}">{y or "n.d."}</a>' for y in years)
    blocks, idx = [], 0
    for y in years:
        blocks.append(f'<h2 class="year-h" id="y{y}" data-y="{y}">{y or "n.d."} <span class="cnt">{sum(1 for q in pubs if q["year"] == y)}</span></h2>')
        for p in (p for p in pubs if p["year"] == y):
            blocks.append(render_pub(p, idx, me, L, TL)); idx += 1
    pubs_html = f"""<div class="years">{ylinks}</div>
<div class="toolbar"><input id="q" type="search" placeholder="{L['search']}"><select id="t">{opts}</select></div>
{''.join(blocks)}<div class="empty">{L['nores']}</div>"""
    W("publications.html", page(ctx, "publications.html", L["pubs"], pubs_html,
                                small_band(L["pubs"], f"{len(pubs)} {L['total']} · {L['source']}")))

    # ---- studenci
    sh = []
    sv = cfg.get("supervision") or {}
    if sv:
        parts = sum(int(sv.get(k) or 0) for k in ("phd", "msc", "beng"))
        tot = int(sv.get("total") or parts)
        items = [(tot, L["sv_all"])]
        if parts == tot:  # podział tylko, gdy jest zgodny z sumą
            items += [(sv.get("msc", 0), L["sv_msc"]), (sv.get("beng", 0), L["sv_beng"]), (sv.get("phd", 0), L["sv_phd"])]
        sh.append(stats_html(items, " inpage"))
    for lvl in ("phd", "ips", "msc", "beng", "intern"):
        group = [s for s in students if s.get("level") == lvl]
        if not group:
            continue
        group.sort(key=lambda s: s.get("status") != "current")
        sh.append(f"<h2>{LEVEL_NAMES[lang][lvl]} <span class=\"cnt\">{len(group)}</span></h2><div class='cards'>")
        for s in group:
            st = s.get("status", "current")
            pill = f'<span class="pill{" on" if st=="current" else ""}">{L[st]}{(" " + str(s["year"])) if s.get("year") else ""}</span>'
            nm = tr(s, "name", lang)
            name = f'<a href="{esc(s["url"])}">{esc(nm)}</a>' if s.get("url") else esc(nm)
            sh.append(f'<div class="card{"" if st=="current" else " done"}"><div class="t">{name}</div>'
                      + (f'<div class="topic">{esc(tr(s, "topic", lang))}</div>' if tr(s, "topic", lang) else "")
                      + (f'<div class="meta">{L["role"]}: {esc(tr(s, "role", lang))}</div>' if s.get("role") else "")
                      + pill + "</div>")
        sh.append("</div>")
    W("students.html", page(ctx, "students.html", L["students"], "".join(sh), small_band(L["students"], L["stud_sub"])))

    # ---- projekty
    ph = []
    for st, label in (("ongoing", L["ongoing"]), ("completed", L["done"])):
        group = [p for p in projects if p.get("status", "completed") == st]
        if not group:
            continue
        ph.append(f"<h2>{label} <span class=\"cnt\">{len(group)}</span></h2><div class='cards'>")
        for p in group:
            ttl = tr(p, "title", lang)
            t = f'<a href="{esc(p["url"])}">{esc(ttl)}</a>' if p.get("url") else esc(ttl)
            meta = " · ".join(esc(x) for x in (tr(p, "funder", lang), p.get("years"),
                              f'{L["role"]}: {tr(p, "role", lang)}' if p.get("role") else None, p.get("partners")) if x)
            ph.append(f'<div class="card{"" if st=="ongoing" else " done"}"><div class="t">{t}</div><div class="meta">{meta}</div>'
                      + (f'<p style="margin:8px 0 0">{esc(tr(p, "description", lang))}</p>' if tr(p, "description", lang) else "")
                      + "</div>")
        ph.append("</div>")
    W("projects.html", page(ctx, "projects.html", L["projects"], "".join(ph), small_band(L["projects"], L["proj_sub"])))

    # ---- działalność
    W("service.html", page(ctx, "service.html", L["service"], sections_html(service, lang),
                           small_band(L["service"], L["serv_sub"])))

    # ---- wystąpienia, współpraca, nagrody
    W("talks.html", page(ctx, "talks.html", L["talks"],
                         sections_html(data["talks"], lang) + f'<p class="note">{L["talks_note"]}</p>',
                         small_band(L["talks"], L["talks_sub"])))
    W("cooperation.html", page(ctx, "cooperation.html", L["coop"], sections_html(data["coop"], lang),
                               small_band(L["coop"], L["coop_sub"])))
    W("awards.html", page(ctx, "awards.html", L["awards"], sections_html(data["awards"], lang),
                          small_band(L["awards"], L["awards_sub"])))

    # ---- dla studentów
    fh = []
    if tr(fs, "intro", lang):
        fh.append(f'<p class="lead">{esc(tr(fs, "intro", lang))}</p>')
    if fs.get("topics"):
        fh.append(f"<h2>{L['topics']} <span class=\"cnt\">{len(fs['topics'])}</span></h2><div class='cards'>")
        for t in fs["topics"]:
            lv = t.get("level", "any")
            kws = "".join(f"<span>{esc(k)}</span>" for k in tr(t, "keywords", lang, []))
            fh.append(f'<div class="card topic-card"><span class="lvl {lv}">{L.get("lvl_" + lv, lv)}</span>'
                      f'<div class="t">{esc(tr(t, "title", lang))}</div>'
                      + (f'<p style="margin:8px 0 0">{esc(tr(t, "description", lang))}</p>' if tr(t, "description", lang) else "")
                      + (f'<div class="kw">{kws}</div>' if kws else "") + "</div>")
        fh.append("</div>")
    if fs.get("offer"):
        fh.append(f"<h2>{L['offer']}</h2><div class='offer'>")
        for o in fs["offer"]:
            fh.append(f'<div><b>{esc(tr(o, "title", lang))}</b>{esc(tr(o, "text", lang))}</div>')
        fh.append("</div>")
    mail = cfg.get("email", "")
    fh.append(f"""<div class="contact-band"><div><h3>{L['howto']}</h3><p>{esc(tr(fs, 'howto', lang))}</p></div>
<a class="cta gold" href="mailto:{esc(mail)}">{L['write']}: {esc(mail)}</a></div>""")
    W("for-students.html", page(ctx, "for-students.html", L["forstud"], "".join(fh), small_band(L["forstud"], L["fs_sub"])))


def build(demo=False, offline=False):
    cfg = load_yaml("config.yaml", {})
    data = {"students": load_yaml("data/students.yaml", []) or [],
            "projects": load_yaml("data/projects.yaml", []) or [],
            "service": load_yaml("data/service.yaml", []) or [],
            "fs": load_yaml("data/for_students.yaml", {}) or {},
            "talks": load_yaml("data/talks.yaml", []) or [],
            "coop": load_yaml("data/cooperation.yaml", []) or [],
            "awards": load_yaml("data/awards.yaml", []) or []}
    pubs, auto_links = collect(demo, offline, cfg)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    global ASSET_V
    ASSET_V = hashlib.md5((CSS + JS).encode()).hexdigest()[:8]
    (OUT / "style.css").write_text(CSS, encoding="utf-8")
    (OUT / "site.js").write_text(JS, encoding="utf-8")
    if (ROOT / "assets").exists():
        shutil.copytree(ROOT / "assets", OUT / "assets", dirs_exist_ok=True)
        if (ROOT / "assets" / "favicon.ico").exists():
            shutil.copy(ROOT / "assets" / "favicon.ico", OUT / "favicon.ico")  # przeglądarki szukają /favicon.ico
    if (ROOT / "assets" / "og.png").exists():
        pass  # gotowe miniatury z tools/make_og.py (styl strony, zdjęcie) – nie nadpisujemy
    elif not make_og_image(cfg, OUT / "assets" / "og.png"):
        print("Pillow niedostępny – używam assets/og.png z repozytorium", file=sys.stderr)
    (OUT / ".nojekyll").write_text("")

    main_lang = cfg.get("lang", "en")
    second = "pl" if main_lang == "en" else "en"
    fs_show = bool(data["fs"].get("show"))
    for lang, prefix in ((main_lang, ""), (second, second + "/")):
        other = second if lang == main_lang else main_lang
        ctx = {"cfg": cfg, "L": I18N[lang], "lang": lang, "prefix": prefix,
               "base": "" if not prefix else "../", "other_lang": other,
               "other_prefix": (second + "/") if not prefix else "../",
               "other_site_prefix": "" if prefix else second + "/", "fs_show": fs_show}
        build_lang(ctx, pubs, auto_links, data, OUT / prefix if prefix else OUT)

    print(f"Gotowe: {len(pubs)} publikacji, wersje {main_lang}+{second} → {OUT}/")


if __name__ == "__main__":
    build(demo="--demo" in sys.argv, offline="--offline" in sys.argv)
