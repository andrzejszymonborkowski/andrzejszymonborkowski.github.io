"""Generuje miniatury do udostępniania (Facebook, LinkedIn): assets/og.png (EN) i assets/og-pl.png (PL).
Uruchom lokalnie po zmianie zdjęcia lub tytułów:  pip install playwright && python tools/make_og.py"""
from pathlib import Path
from playwright.sync_api import sync_playwright
T = Path(__file__).parent
V = {
 "og.png": dict(KICK="Warsaw University of Technology", NAME="Andrzej Szymon<br>Borkowski",
   SUB="Assoc. Prof. DSc PhD Eng.<br>BIM · BIM–GIS integration · formal ontologies<br>artificial intelligence in construction",
   FOOT="Publications · Projects · Talks · Students"),
 "og-pl.png": dict(KICK="Politechnika Warszawska", NAME="Andrzej Szymon<br>Borkowski",
   SUB="dr hab. inż., profesor uczelni<br>BIM · integracja BIM–GIS · ontologie formalne<br>sztuczna inteligencja w budownictwie",
   FOOT="Publikacje · Projekty · Wystąpienia · Studenci"),
}
src = (T / "og_template.html").read_text(encoding="utf-8").replace("{DOTR}", "410").replace("{DOTT}", "213")
with sync_playwright() as p:
    b = p.chromium.launch(**({"executable_path": "/usr/bin/google-chrome"} if Path("/usr/bin/google-chrome").exists() else {}),
                          args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1200, "height": 630})
    for name, v in V.items():
        h = src
        for k, val in v.items():
            h = h.replace("{" + k + "}", val)
        f = T / "_og.html"; f.write_text(h, encoding="utf-8")
        pg.goto(f.resolve().as_uri()); pg.wait_for_timeout(600)
        pg.screenshot(path=str(T.parent / "assets" / name))
        f.unlink()
    b.close()
print("OK")
