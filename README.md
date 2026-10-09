# AJ — panel mobilny i generowanie wideo H3

Projekt przyjmuje **6 obrazów i opcjonalnie 1 plik audio**, analizuje referencje, przygotowuje 10 wariantów promptu i generuje wideo przez ComfyUI na GPU Vast.ai. Docelowym domyślnym wariantem pozostaje native MiniMax H3 INT8; 10Eros to opcjonalny wariant porównawczy. Dokładny stos produkcyjnych modeli nie jest jeszcze zamrożony.

**Stan po audycie CPU/runtime, 8 października 2026:** wszystkie znane merge-blocking problemy wykryte w STEP 1–3 i cross-step zostały naprawione, niezależnie zweryfikowane i scalone do `main` w PR #1. Końcowy merge commit to `4ea3fdd1b9d41e2475c52ed5f705b52af35df5b0`.

Baseline z 8 października przechodzi:

- `make setup`;
- `make test`: **290 testów PASS + 16 HTTP smoke checks**;
- `make audit`: **290 testów PASS**;
- finalne targeted suites dla dwóch ostatnich blockerów G/C: **10/5 PASS**;
- zero failures, errors i skips; bez hung teardown.

To oznacza **CPU/runtime acceptance PASS**. Nie oznacza jeszcze pełnego GPU acceptance: na aktualnym kodzie po merge nie wykonano jeszcze realnej instalacji na docelowym RTX PRO 6000, prawdziwego `/object_info`, prompt-only acceptance, renderu H3, benchmarku VRAM ani lifecycle STOP/DESTROY na wynajętej instancji.

**Feature set offline/CPU, 9 października 2026**, na gałęzi
`feat/h3-cpu-product-completion`: `make setup` PASS, `make test`
**358 testów PASS + 16 HTTP smoke checks**, `make audit` **358 testów PASS**; zero failures, errors
i skips, zakończony teardown. Dodano 68 testów dla MP4, prefetch, Diagnostics,
Live Logs i opcjonalnego terminala. [Raport CPU i self-review](docs/CPU_PRODUCT_ACCEPTANCE_2026-10-09.md)
opisuje dokładne testowane źródła, izolację środowiska testowego i ograniczenia
weryfikacji CPU. GPU/live acceptance nadal jest pending.

Aktualna kolejność produktu: **feature set offline/CPU -> pełna regresja/audyt CPU
-> osobny wybór i zamrożenie finalnych modeli -> jedna skonsolidowana sesja
Vast/GPU na końcu**. Manifest modeli pozostaje provisional. Szczegóły funkcji,
Advanced, prefetch i opcjonalnego terminala: [h3/README.md](h3/README.md).

## Od czego zacząć

1. [Roadmap produktu i aktualny scope](docs/ROADMAP.md) — kanoniczny plan dalszych etapów i rzeczy świadomie odłożonych.
2. [CPU feature acceptance i self-review](docs/CPU_PRODUCT_ACCEPTANCE_2026-10-09.md) — stan finalnego feature set na gałęzi PR; [baseline z 8 października](docs/FINAL_CPU_ACCEPTANCE_2026-10-08.md) opisuje wcześniejszy stan po merge.
3. [Przygotowanie i acceptance na Vast GPU](docs/GPU_ACCEPTANCE.md) — końcowa sesja po feature set, regresji CPU i osobnym wyborze modeli.
4. [H3 Vast Mobile](h3/README.md) — wymagania runtime, persistent volume i sterowanie usługami.
5. [Historyczny audyt z 6 października](docs/AUDIT_2026-10-06.md) — źródło wcześniejszych blockerów; nie jest już aktualnym statusem produkcyjnym.
6. [Oryginalny stan projektu](migration/00_START_HERE/CURRENT_STATE.md) — materiał migracyjny i historia decyzji.

Materiały pod `migration/` i `archive/` są baseline'em historycznym. Ich stare oznaczenia PASS/FAIL nie zastępują aktualnego stanu na `main`.

## Praca lokalna / Codex

Wymagane: Linux, Python >=3.11 (development checks używają 3.12), Git i Bash. Node.js jest wymagany do pełnej regresji frontendu; sam backend go nie potrzebuje.

```bash
make setup
make test
make audit
make panel
make status
make stop
```

`make test` i `make audit` są teraz oczekiwane jako PASS na baseline po merge. Testy CPU obejmują m.in. persistence/recovery, lifecycle fencing, procesy i pidfd ownership, provisioning/deployment, workflow validation, frontend races, proxy cleanup i HTTP boundaries.

Panel developerski działa lokalnie i nie jest równoważny deploymentowi GPU. CPU PASS nie dowodzi jakości renderu, poprawnego załadowania wag, zachowania VRAM ani prawdziwych operacji Vast.

## Pierwszy deployment Vast

Używaj **aktualnego** instalatora z `h3/INSTALL_ON_VAST.sh`, nie archiwalnej kopii z `migration/`.

Na docelowej instancji:

```bash
cd h3
bash INSTALL_ON_VAST.sh
```

Po obecności wszystkich wymaganych modeli:

```bash
bash <PANEL_ROOT>/scripts/preflight.sh
bash <PANEL_ROOT>/scripts/smoke_test.sh
```

Nie uruchamiaj pełnego batcha jako pierwszego testu GPU. Po preflight/smoke wykonaj najpierw kontrolowany prompt-only acceptance, a następnie **jeden** natywny render H3. Szczegóły i kryteria zaliczenia są w [docs/GPU_ACCEPTANCE.md](docs/GPU_ACCEPTANCE.md).

## Układ repozytorium

| Katalog | Zawartość |
| --- | --- |
| `h3/` | Aktualny, naprawiony runtime, provisioning, panel i workflowy |
| `tests/` | CPU/runtime regression suite |
| `docs/` | Aktualne acceptance, audyty i wymagania GPU |
| `migration/` | Oryginalne materiały migracyjne zachowane jako baseline |
| `archive/` | Archiwalne załączniki |
| `scripts/` | Narzędzia developerskie i testy |
| `requirements/` | Pinned dependencies / locki |
| `.github/workflows/` | Definicje automatyzacji repozytorium |
| `.local/`, `.venv/` | Lokalny stan i zależności; poza Git |

## Zasada kosztowa

Przed pierwszym płatnym renderem wymagane są: poprawny persistent volume, poprawne modele i custom nodes, oba ComfyUI workers, panel, `preflight.sh` i `smoke_test.sh`.

Prompt-only acceptance ma być wykonany bez uruchamiania pełnego panelowego batcha renderów. Dopiero po nim uruchamiamy jeden kontrolowany render i weryfikujemy wynik w `/history` oraz plik wideo.
