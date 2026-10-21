# GPU Acceptance Phase 1 — raport przygotowania CPU

Repo: `ziomalskit/ziomalskit`, istniejący Draft PR #8,
`feat/dual-llm-profiles`. Punkt wyjścia
`bf802ea118db848e35714b259d0f9246d3956144`, baza PR #6
`84af26e26c0ec85a40efe712e5e32506c5a9af3f`, zależność PR #5
`c6f033ab41a4456fd55558e652a563d8c31418d1`.
Wszystkie trzy PR pozostają Draft/open; nie wykonano merge.

## Zmiany

| Plik | Wynik |
| --- | --- |
| [GPU_ACCEPTANCE_PHASE1.md](GPU_ACCEPTANCE_PHASE1.md) | Pełny runbook A–D, wymagania Blackwell/arch120/CUDA, primary sources Vast/NVIDIA, warianty przygotowania bez drogiego GPU, exact disk plan, kosztorys bez fikcyjnych cen, GO/STOP/deadlines, manual model probes, private evidence, stop/resume i lista decyzji przed pierwszym kosztem. |
| `h3/config/gpu_acceptance_session.example.json` | Ścisły formularz operatora: wszystkie limity, quote/storage facts i billing pozostają null; approvals false. Żadnej arbitralnej ceny, limitu ani zgody. |
| `h3/scripts/gpu_acceptance_operator.py` | Domyślny readonly plan, osobne A/B/C/D scopes, jawne inspect/watch. Kontrola approved source lock, aktualnego clean checkout/v20, combined hourly/total cost, miejsca/staging/reserves, actual mount/GPU/CUDA/cgroups i wyłączonych production services. Nie ma inferencji ani wywołań Vast/lifecycle. |
| `h3/scripts/model_acceptance.py` | Existing full verify receipt dodatkowo zwraca już zweryfikowany stat signature. Monitor C wiąże późniejsze pomiary z tymi samymi bajtami po pełnym SHA256, bez rehashu 113 GB co próbkę. Pełne weryfikacje nadal są wymagane przed C. |
| `tests/test_gpu_acceptance_operator.py` | 27 testów CPU: brak GPU/dysku, zły mount/sprzęt/CUDA, missing limits, quote/cost, stale/cofany billing i immutable session start, foreign PID/services, incomplete/wrong models, file mutation after hash, interruption, private exclusive evidence i brak credential/inference/lifecycle side effects. Ceny/urządzenia/wagi w fixtures są syntetyczne. |
| `tests/test_model_acceptance.py` | Dodatkowe sprawdzenie zgodności signature z istniejącym full-file/native verifier; żaden wcześniejszy assert nie został usunięty. |
| README, h3 README, GPU_ACCEPTANCE, ROADMAP | Link do ograniczonej Phase 1, zakończonej przed deploymentem, renderami i lifecycle. Pełne późniejsze gate’y zachowane. |

Nie zmieniono manifestu, source approvals, profili, native parsera/runtime,
workflow, cache, render priority, leases ani progów VRAM. Production provisioning
nadal odrzuca wymagany pending DavidAU; nie istnieje generic allow_pending.

## Granice narzędzia i dowodów

- Plan nie kontaktuje się z providerem ani nie zapisuje środowiska.
- Inspect wykonuje lokalne readonly diagnostics, a C również existing pełne
  SHA256/GGUF/native pin checks; `--version` nie jest GPU inference.
- Watch wymaga osobnej zgody B/C, aktualnego rzeczywistego billingu, stałych
  approvals/quote/limits i prywatnego katalogu. Evidence tworzy wyłącznie jawny
  `--watch`, bez overwrite; STOP reasons nie ujawniają sekretów.
- Monitor nigdy nie kończy procesu native ani nie zatrzymuje billingu providera.
  Operator nadzoruje osobne build/download deadlines i sam przerywa własne
  zadania. GNU timeout ogranicza jedynie wywołaną komendę/grupę procesu.
- Sampled peak VRAM jest dolnym ograniczeniem peak. Nie zmierzono KV/workspace,
  load time ani tokens/s. Nie zastąpiono tych pomiarów rozmiarem GGUF.
- Step 4 receipt sprawdza istniejący parser i temporal contract. Step 3 wymaga
  review native EOS/finish/no truncation oraz istniejącego normalize/canonical
  plan. Snapshot instrukcji nie jest gotowym effective inputem; brak actual
  v20 lineage blokuje controlled full Step 3/4, nie prowadzi do fixture PASS.
- Żaden wynik helpera/checkera nie attests GPU acceptance ani nie retire'uje
  DavidAU. Jest wymagany osobny review rzeczywistych wyników Qwen3.5-27B.

## Źródła i niezmienność

11 approved GGUF: **113395969792 B**, początkowy models/staging floor
**132795578176 B**, największy staging **19399608384 B**. Dodatkowe rezerwy
build/venv/evidence/transfer oraz operacyjny disk floor wybiera operator.

Immutable repositories/revisions/sizes/SHA pozostają takie jak w
`h3/config/model_acceptance.json` i `h3/config/models_manifest.json`;
[bootstrap report](MODEL_ACCEPTANCE_BOOTSTRAP_2026-10-14.md) zawiera dowód
Git LFS Qwen3-14B. Nie pobrano żadnych rzeczywistych wag.

Runtime: llama.cpp `60eeeb6082c1126bb8bc72902c83123cd056811b` / `b10472`,
LLM node `65983de33681816f856db7e16767da906084c688`, arch120, plan CUDA13.0.
Oba stacki i Step 4 reasoning 6144/8192, final8192/framing64,
max14400/16448, context32768/36864 pozostają bez zmian. Step 3 OFF.

Chroniony v20: **3084071 B**, SHA256
`276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`.
Porównanie z `bf802ea` potwierdza byte-for-byte 78 chronionych plików,
w tym wszystkie śledzone `migration/` i `archive/`.

## Walidacja CPU

| Kontrola | Wynik |
| --- | --- |
| `make setup` | PASS; hash-pinned packages, pip check bez broken requirements. |
| `make test` finalnego kodu | **906 PASS**, **408.315 s** + **16 HTTP smoke checks**; no ComfyUI submission/Vast action. |
| `make audit` finalnego kodu | **906 PASS**, **410.660 s**; wszystkie **7** baseline defects nadal wykrywane, baseline untouched. |
| Focused operator/bootstrap/profile tests | **68 PASS**, **7.943 s**; w tym **27** nowych testów operatora. |
| Local panel CPU | Owned start, authenticated HTML/config check i owned stop: PASS. GPU workers nie są instalowane. |
| Runbook syntax | **18** bash blocks przez bash -n, **4** multiline Python snippets przez AST; native model filenames zgodne z manifestem, nowe links istnieją. Nie wykonywano tych komend inference. |
| Actual readonly CLI smoke | Existing source --plan PASS, empty operator template BLOCKED z exit1; nie utworzono store. |
| `git diff --check` | PASS. |
| Final failures/errors/skips | **0 / 0 / 0** w obu finalnych pełnych checks. |
| Functional freeze | **229** niemarkdownowych source/fixture files; hashes zachowane przez finalną regresję i pełny audyt. |

Review rzeczywistego billingu doprowadził do dodatkowych guardów: external
preparation jest rezerwowane również podczas monitorowania, session start
nie może się zmienić, a invoice timestamp/total nie mogą się cofnąć.
Testy obejmują prawidłową atomową aktualizację i STOP przy próbie resetu,
bez zmiany produkcji ani wcześniejszych tolerancji. Pierwszy pełny run przed
finalnymi billing guardami również przeszedł; poniższe CI i tabela dotyczą
powtórzonej walidacji finalnego kodu.
Wyniki CI dla finalnego commita oraz oba URL (`push` i `pull_request`)
są publikowane w opisie istniejącego [Draft PR #8](https://github.com/ziomalskit/ziomalskit/pull/8).
Workflow bez zmian uruchamia `make setup`, `make test`, `make audit`.
Nie pomija testów ani nie zmienia timeoutów/tolerancji.

## Status przed pierwszą sesją

**READY do audytu przygotowań; BLOCKED dla pierwszego kosztu i realnych prób.**
Brakuje aktualnej konkretnej oferty i cen, wszystkich zatwierdzonych limitów,
storage/transfer/retention planu, actual GPU/mount/toolchain facts po dostępie,
prywatnego rzeczywistego case/lineage oraz decyzji operatora. Dostępności
CPU-only/tańszego compute na docelowej machine nie potwierdzono.

Dokładna lista siedmiu wymaganych zatwierdzeń przed kosztem znajduje się w
[runbooku §9](GPU_ACCEPTANCE_PHASE1.md#9-co-operator-musi-zatwierdzić-przed-pierwszym-kosztem).
Po tych przygotowaniach praca się kończy; kolejny krok wymaga osobnej decyzji
operatora. Nie uruchomiono GPU/Vast, downloads/inference/renderów ani paid
resources. Nie wykonano STOP/DESTROY ani jakiejkolwiek zmiany bramki produkcji.
