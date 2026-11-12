"""Generuje ikonki strony (karta przeglądarki, telefon): assets/favicon-*.png, apple-touch-icon.png, favicon.ico.
Uruchom: python tools/make_favicon.py"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image
T = Path(__file__).parent; A = T.parent / "assets"
with sync_playwright() as p:
    b = p.chromium.launch(**({"executable_path": "/usr/bin/google-chrome"} if Path("/usr/bin/google-chrome").exists() else {}), args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 512, "height": 512})
    for name, r in (("_round.png", 112), ("_square.png", 0)):
        f = T / "_fav.html"; f.write_text((T / "favicon_template.html").read_text().replace("{R}", str(r)))
        pg.goto(f.resolve().as_uri()); pg.wait_for_timeout(500)
        pg.screenshot(path=str(T / name), omit_background=True, clip={"x": 0, "y": 0, "width": 512, "height": 512}); f.unlink()
    b.close()
big = Image.open(T / "_round.png").convert("RGBA")
for s in (16, 32, 48, 192, 512):
    big.resize((s, s), Image.LANCZOS).save(A / f"favicon-{s}.png")
big.resize((48, 48), Image.LANCZOS).save(A / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
# iPhone sam zaokrągla rogi, więc ikona bez zaokrągleń
Image.open(T / "_square.png").convert("RGB").resize((180, 180), Image.LANCZOS).save(A / "apple-touch-icon.png")
for n in ("_round.png", "_square.png"): (T / n).unlink()
print("OK")
