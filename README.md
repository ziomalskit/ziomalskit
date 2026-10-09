# AJ — panel mobilny i generowanie wideo H3

Projekt przyjmuje **6 obrazów i opcjonalnie 1 plik audio**, analizuje referencje, przygotowuje 10 wariantów promptu i generuje wideo przez ComfyUI na GPU Vast.ai. Produkcyjne profile to wyłącznie **H3 Full** i **10Eros Full**, ze wspólną, dokładnie zachowaną logiką promptów **v20 Heretic**. Legacy INT8 pozostaje tylko jawną ścieżką diagnostyczną.

**Stan po audycie CPU/runtime, 8 października 2026:** wszystkie znane merge-blocking problemy wykryte w STEP 1–3 i cross-step zostały naprawione, niezależnie zweryfikowane i scalone do `main` w PR #1. Końcowy merge commit to `4ea3fdd1b9d41e2475c52ed5f705b52af35df5b0`.

Baseline z 8 października przechodzi:

- `make setup`;
- `make test`: **290 testów PASS + 16 HTTP smoke checks**;
- `make audit`: **290 testów PASS**;
- finalne targeted suites dla dwóch ostatnich blockerów G/C: **10/5 PASS**;
- zero failures, errors i skips; bez hung teardown.

To oznacza **CPU/runtime acceptance PASS**. Nie oznacza jeszcze pełnego GPU acceptance: na aktualnym kodzie po merge nie wykonano jeszcze realnej instalacji na docelowym RTX PRO 6000, prawdziwego `/object_info`, prompt-only acceptance, renderu H3, benchmarku VRAM ani lifecycle STOP/DESTROY na wynajętej instancji.

**Zaakceptowany feature set z PR #4, 9 października 2026**, scalony do `main`
w commicie `bfcc2bb22ff9691fda924fe17bd060a7e131c2e2`: `make setup` PASS, `make test`
**358 testów PASS + 16 HTTP smoke checks**, `make audit` **358 testów PASS**; zero failures, errors
i skips, zakończony teardown. Dodano 68 testów dla MP4, prefetch, Diagnostics,
Live Logs i opcjonalnego terminala. [Raport CPU i self-review](docs/CPU_PRODUCT_ACCEPTANCE_2026-10-09.md)
opisuje dokładne testowane źródła, izolację środowiska testowego i ograniczenia
weryfikacji CPU. GPU/live acceptance nadal jest pending.

**Migracja modeli:** [raport i ograniczenia](docs/MODEL_MIGRATION_2026-10-09.md).
`make setup` PASS, `make test` **439 PASS + 16 HTTP checks**, `make audit`
**439 PASS**, focused migration checks **81 PASS**; zero failures/errors/skips.
17 artefaktów ma niezależnie sprawdzone immutable revisions, dokładne rozmiary
i SHA256. Pełne metadata źródłowego 9B BF16 writera nadal blokuje zamrożenie
całego manifestu; provisioning i production preflight zatrzymują się na tym
braku. To nie jest jeszcze stan gotowy do końcowej sesji GPU. Nie wykonano
realnych działań Vast ani inference/renderowania GPU.

Kolejność: **zamknięcie provenance 9B -> provisioning/preflight -> jedna
skonsolidowana sesja Vast/GPU**. Szczegóły profili, pamięci, persistent storage,
LoRA i tokenów jednorazowych: [h3/README.md](h3/README.md).

## Od czego zacząć

1. [Roadmap produktu i aktualny scope](docs/ROADMAP.md) — kanoniczny plan dalszych etapów i rzeczy świadomie odłożonych.
2. [CPU feature acceptance i self-review](docs/CPU_PRODUCT_ACCEPTANCE_2026-10-09.md) — stan finalnego feature set na gałęzi PR; [baseline z 8 października](docs/FINAL_CPU_ACCEPTANCE_2026-10-08.md) opisuje wcześniejszy stan po merge.
3. [Przygotowanie i acceptance na Vast GPU](docs/GPU_ACCEPTANCE.md) — końcowa sesja po pełnym zamrożeniu provenance modeli.
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

Installer pobiera i weryfikuje zamrożone artefakty na persistent volume oraz
pozostawia `H3_ALLOW_SUBMISSIONS=0`. Dopóki provenance 9B jest niekompletne,
instalacja zatrzymuje się przed pobieraniem wag. Po zamknięciu tego blokera:

```bash
bash <PANEL_ROOT>/scripts/preflight.sh
bash <PANEL_ROOT>/scripts/smoke_test.sh
```

Nie uruchamiaj pełnego batcha jako pierwszego testu GPU. Po preflight/smoke
wykonaj kontrolowany prompt-only acceptance, potem pojedyncze rendery Full
i przełączenie H3 → 10Eros → H3. Full/BF16 H3 ma historyczne zgłoszenia czarnych
klatek na niektórych konfiguracjach ComfyUI/Blackwell; CPU nie dowodzi naprawy.
Szczegóły są w [docs/GPU_ACCEPTANCE.md](docs/GPU_ACCEPTANCE.md).

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
