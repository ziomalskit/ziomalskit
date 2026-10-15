# AJ — panel mobilny i generowanie wideo H3

Projekt przyjmuje **6 obrazów i opcjonalnie 1 plik audio**, analizuje referencje, przygotowuje 10 wariantów promptu i generuje wideo przez ComfyUI na GPU Vast.ai. Produkcyjne profile to wyłącznie **H3 Full** i **10Eros Full**, ze wspólną, dokładnie zachowaną logiką promptów **v20 Heretic**. Legacy INT8 pozostaje tylko jawną ścieżką diagnostyczną.

**Draft PR #8: dwa profile LLM (14 października 2026).** Step 1/2 używa **llmfan46 Mistral Small 3.2 24B Instruct 2506 Ultra Uncensored Heretic**: Q4_K_M dla H3 Full, Q6_K dla 10Eros Full, z opublikowanym dla tego wariantu projektorem BF16. Writery to nadal Qwen3-14B Heretic Q4_K_M i Qwen3.5-27B Heretic Q5_K_M. Step 4 ma reasoning ON i budget odpowiednio **6144 / 8192**, z 8192 tokenami na final; Step 3 pozostaje OFF. Step 0 i jego cache są wspólne, cache Step 1/2 wiąże model, kwantyzację, projektor i instrukcje. Metadane Qwen3-14B zostały potwierdzone z immutable Git LFS commita. [Aktualny raport i izolowany bootstrap acceptance](docs/MODEL_ACCEPTANCE_BOOTSTRAP_2026-10-14.md) pozwala przygotować 11 zatwierdzonych modeli oraz native runtime bez uruchamiania usług/inferencji. Nieużywany DavidAU 9B usunięto z aktywnego manifestu; integralność aktualnych wag jest wymagana, ich GPU acceptance pozostaje pending.

Draft PR #6 dodaje wybór **Autoprompt / Own final H3 prompt**. Autoprompt zachowuje
10 kandydatów (5 auto + 5 review). Własny finalny prompt przechodzi lokalną
walidację i trafia bez zmian do renderu: bez Prompt Workera, Step 0–4 i generowania
LLM, po jednym renderze na batch. Referencje, audio, oba profile, LoRA, wszystkie
render controls oraz duration/recovery pozostają aktywne. Ten tryb omija writer,
a używany writer 10Eros to Qwen3.5-27B Q5_K_M. Szczegóły:
[H3 prompt modes](h3/README.md#profiles-prompts-and-loras) i
[workflow adapters](docs/WORKFLOW_ADAPTERS.md#prompt-sources-autoprompt-and-own-final-h3-prompt).

Remediacja niezależnego review PR #6 obejmuje jeden zamrożony rejestr LoRA,
pełną tożsamość pakietu natywnego i sprawdzanie wdrożonych plików, zgodność
retry z BASE, widoczność kwarantanny, parser czasu, własność recovery ręcznych
renderów, osobne ścieżki obrazu/audio oraz wąskie zakresy LoRA.
[Dokładny kontrakt i regresje F1–F8](docs/WORKFLOW_ADAPTERS.md#independent-adversarial-remediation-2026-10-12).

**Poprawki Ultra w Draft PR #5:** [raport i dowody testowe](docs/PR5_ULTRA_FIXES_2026-10-09.md).
Terminal pozostaje niedostępny w produkcji, również przy `H3_ENABLE_TERMINAL=1`.
GPU/live acceptance nadal pozostaje pending. Bieżąca instalacja używa wyłącznie modeli z aktywnego, zweryfikowanego manifestu.

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

**Personal / Ephemeral Deployment:** [aktualny runbook](docs/PERSONAL_FIRST_RUN.md).
Jedna osoba, telefon, dysk instancji `/workspace`, jeden store wag, domyślne19
artefaktów278009016136B. Local Volume i23rendery nie są wymagane. Budżet pierwszej
sesji <=$15/4h wymaga realnej oferty, przepustowości i kosztorysu. Minimalna
rzeczywista próba27B poprzedza autoprompt10Eros; potem dwa pojedyncze rendery,
pobranie MP4 i ręczny DESTROY. Wagi można instalować przed GPU acceptance;
nie ma obowiązkowej procedury DavidAU retirement. [Pełne Gate1–6](docs/GPU_PRODUCTION_SESSION.md)
pozostają opcjonalną diagnostyką. Starsze datowane raporty opisują historię,
nie aktualne wymaganie volume, DavidAU ani dwóch store.

## Od czego zacząć

[Poprawki F1–F4 w PR #8](docs/BUGFIX_F1_F4_2026-10-14.md) dodają trwałą bramkę
kosztową, natywne potwierdzenie EOS dla Step 0–3 oraz atomową publikację uploadów
i GGUF. Modele i v20 pozostają zachowane; GPU acceptance nadal pending.

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

Installer weryfikuje wszystkie źródła/revisions/size/SHA256, pobiera brakujące
wagi i pozostawia `H3_ALLOW_SUBMISSIONS=0`. Domyślny ephemeral dysk nie daje
retencji po DESTROY. Zweryfikowany Local Volume jest wymagany wyłącznie dla
funkcji keep-data. Przed płatną pracą wykonaj preflight/smoke i rzeczywistą
próbę bieżącego writera według [Personal First Run](docs/PERSONAL_FIRST_RUN.md).
Nie używaj normalnego batcha jako testu jednego renderu: tworzy5auto-renderów.
Full/BF16 na Blackwell, realne MP4 i VRAM nadal wymagają sprawdzenia na GPU.

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

Przed pierwszym płatnym renderem wymagane są: zweryfikowany dysk instancji z zapasem miejsca, poprawne modele i custom nodes, oba ComfyUI workers, panel, `preflight.sh` i `smoke_test.sh`.

Prompt-only acceptance ma być wykonany bez uruchamiania pełnego panelowego batcha renderów. Dopiero po nim uruchamiamy jeden kontrolowany render i weryfikujemy wynik w `/history` oraz plik wideo.
