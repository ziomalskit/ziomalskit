# Final CPU/runtime acceptance — 2026-10-08

## Status

**PASS — SAFE TO PROCEED TO GPU/LIVE ACCEPTANCE**

Branch z poprawkami `fix/aj-runtime-blockers` został scalony do `main` przez PR #1.

- finalny fix SHA: `cd4e38ea595b1d13caef371007057b8f12c6937e`
- parent finalnego fixa: `88a9102d3e652632ef3834f2e309690768bdf45f`
- merge commit na `main`: `4ea3fdd1b9d41e2475c52ed5f705b52af35df5b0`

## Zakres zweryfikowanych blockerów

Audyt objął trzy etapy oraz późniejszy cross-step verification:
- STEP 1: persistence/state/recovery/process-launch invariants;
- STEP 2: process/filesystem/provisioning/deployment;
- STEP 3: HTTP/frontend/workflow/API/proxy;
- cross-step interactions pomiędzy powyższymi.

Cross-step verification wykrył dwa ostatnie P1:
- G: maintenance deferral + persistence failure mógł zrobić terminal failure mimo pewnego braku POST;
- C: cancellation restartu podczas TERM grace mogło porzucić żywego service descendanta.

Oba zostały naprawione w jednym finalnym commicie i niezależnie zweryfikowane.

## Final two-blocker verification

### G — PASS

Niezależny repro używał rzeczywistych workerów, produkcyjnego zapisu kolejki, lokalnego HTTP i fault injection na `os.fsync`.

Potwierdzono m.in.:
- zero POST podczas degraded persistence;
- queued/deferred pozostaje retryable;
- persistence fence blokuje następny dispatch;
- po repair dokładnie jeden POST na job;
- render kończy się `completed`;
- prompt kończy się `pending_review`;
- validation/converter/rejection pozostają terminalne;
- armed/attempting/accepted/uncertain pozostają recovery, bez automatic requeue.

Nowe regresje: **10/10 PASS**.

### C — PASS

Niezależny real-process repro używał rzeczywistego `restart_local_service`, `service_ctl`, dedicated service group i descendanta ignorującego TERM.

Potwierdzono m.in.:
- cancellation/repeated cancellation nie porzuca service group;
- descendant ginie przed zakończeniem cleanup;
- ten sam original pidfd jest używany do bezpiecznej eskalacji;
- pierwszy KILL error i transient membership-scan error są bezpiecznie obsługiwane;
- sole owner/pidfd/PID record pozostają zachowane przy niepewności;
- foreign process pozostaje nietknięty;
- brak numeric `killpg`.

Nowe regresje: **5/5 PASS**.

## Końcowa walidacja

- `make setup`: PASS
- `make test`: **290 tests PASS + 16 HTTP smoke checks**
- `make audit`: **290 tests PASS**
- targeted G/C: **10 / 5 PASS**
- STEP 1 reliability: **39 PASS**
- STEP 2 process/filesystem/provisioning: **69 PASS**
- STEP 3 API/workflow/frontend/proxy: **72 PASS**
- controller/persistence/recovery: **95 PASS**
- `git diff --check`: PASS
- **0 failures, 0 errors, 0 skips, no hung teardown**

W finalnej weryfikacji nie wykonano GPU renders, real Vast actions, external paid API calls ani użycia real credentials.

## Co ten PASS oznacza

Ten dokument zamyka CPU/runtime merge gate. Nie jest dowodem poprawności realnej inferencji GPU, jakości filmu, peak VRAM, loaderów na docelowym ComfyUI ani operacji Vast na faktycznej instancji.

Następny etap: `docs/GPU_ACCEPTANCE.md`.

Status po merge:

**CPU/runtime accepted; GPU/live acceptance pending.**
