# Osobista strona naukowa – instrukcja

Strona jest generowana automatycznie:
- **publikacje** pobierane codziennie z ORCID (0000-0002-7013-670X),
- **studenci, projekty, funkcje** – wpisywane ręcznie w prostych plikach tekstowych.

## Jednorazowe uruchomienie (ok. 15 minut)

1. Załóż konto na github.com (jeśli nie masz) i utwórz nowe repozytorium,
   np. `borkowski-site` (Public).
2. Wgraj całą zawartość tego folderu (przycisk *Add file → Upload files*,
   przeciągnij wszystkie pliki i katalogi, w tym `.github`).
3. W repozytorium: **Settings → Pages → Source: GitHub Actions**.
4. **Actions → „Buduj i publikuj stronę” → Run workflow**.
   Po ok. minucie strona będzie pod adresem `https://<login>.github.io/borkowski-site/`.
5. (Opcjonalnie) własna domena, np. `borkowski.pl`: Settings → Pages → Custom domain.

## Codzienna obsługa

| Chcę… | Co robię |
|---|---|
| dodać studenta | edytuję `data/students.yaml` (ołówek na GitHubie), kopiuję blok, zapisuję *Commit* |
| dodać projekt/grant | `data/projects.yaml` |
| dodać funkcję, redakcję, komitet | `data/service.yaml` |
| zmienić bio, zdjęcie, linki | `config.yaml`; zdjęcie wgrywam jako `assets/photo.jpg` |
| nowa publikacja | dodaję ją w Bazie Wiedzy PW → Profil → *Eksportuj dorobek do ORCID*; strona zaktualizuje się następnego dnia (albo od razu: Actions → Run workflow) |
| ukryć/dopisać publikację | `data/publications_overrides.yaml` |
| dorzucić publikacje spoza ORCID | eksport BibTeX z Bazy Wiedzy zapisany jako `data/extra.bib` (duplikaty są usuwane automatycznie po DOI i tytule) |

Każda zmiana zapisana na GitHubie przebudowuje stronę w ok. minutę.

## Dlaczego ORCID, a nie bezpośrednio Baza Wiedzy PW?
Baza Wiedzy blokuje automatyczne pobieranie (robots.txt) i nie ma publicznego API.
ORCID ma stabilne, darmowe API, a BW ma wbudowany eksport do ORCID – więc
BW pozostaje jedynym miejscem, gdzie wprowadzasz publikacje.

## Lokalnie (opcjonalnie)
```
pip install pyyaml
python build.py            # pobiera z ORCID, wynik w public/
python build.py --demo     # podgląd na przykładowych danych
```
