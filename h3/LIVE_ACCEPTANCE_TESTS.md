# Live acceptance tests — run in this order

Baseline CPU/runtime jest zaakceptowany. Ten dokument dotyczy wyłącznie testów na realnym środowisku GPU/Vast.

Nie uruchamiaj pełnego panelowego batcha przed przejściem kroków 1–3.

## 1. Infrastructure preflight

```bash
bash scripts/preflight.sh
```

Musi potwierdzić:
- ComfyUI 0.38.0;
- comfy-cli 1.21.0 (warning jeśli nie jest dokładnie zgodne);
- CUDA toolkit >=12.8;
- wszystkie wymagane modele/default LoRAs/Bunny model;
- Linux llama-cli;
- oba Comfy services;
- authenticated panel;
- runtime na Linuxie obsługującym wymagane pidfd process-group signalling.

## 2. Conversion smoke test — NO REAL RENDER

```bash
bash scripts/smoke_test.sh
```

Test patchuje techniczny workflow i wykonuje walidację/konwersję obu usług. Ścieżka `--print-prompt` ma zakończyć się bez wysłania generacji.

Sprawdź również rzeczywiste `/object_info` obu usług i brak UI-only classes w skonwertowanym API graphie.

## 3. Prompt-only acceptance — bez render batcha

Nie używaj zwykłego endpointu tworzenia batcha jako pierwszego testu, ponieważ standardowy panelowy flow przygotowuje kandydatów do auto-renderu.

Uruchom oddzielny prompt-only workflow z sześcioma małymi obrazami i bez audio.

Potwierdź:
- Step 0–4 kończy się sukcesem;
- finalny prompt zostaje przechwycony;
- brak POST do render workera;
- cache Step 0–2 działa tam, gdzie powinien;
- Step 3/4 dostają candidate seed;
- prompt worker pozostaje zdrowy po zakończeniu.

## 4. One native H3 render

Uruchom dokładnie jeden kontrolowany render.

Zweryfikuj:
- native H3 INT8;
- właściwe LoRA i model files;
- właściwy final prompt;
- dokładnie jeden submission;
- sukces w `/history`;
- realny MP4 i playback przez controller proxy;
- peak VRAM, wall time i brak OOM/restart loop.

Dopiero po tym można uruchomić normalny panelowy batch.

## 5. Parallel overlap benchmark

Podczas jednego H3 renderu przygotuj następny prompt batch.

Porównaj render wall time z render-only baseline. Jeżeli penalty jest istotne, rozważ prebuffer/idle-only dla prompt workera zamiast stałego overlapu.

## 6. Watchdog i recovery

Na disposable jobie:
- ustaw krótki timeout;
- potwierdź cancel/restart tylko właściwej usługi;
- sprawdź continuation kolejki;
- sprawdź restart panelu i recovery bez duplicate submit.

## 7. Vast lifecycle

Tylko gdy persistent storage raportuje VERIFIED i ręcznie potwierdzono Local Volume:
- STOP AFTER CURRENT;
- start tej samej instancji z Vast i weryfikacja onstart;
- STOP AFTER QUEUE;
- ponowny start i weryfikacja queue/state;
- DESTROY COMPUTE / KEEP DATA dopiero na końcu, po potwierdzeniu output/state na Local Volume.

Pełna checklista i końcowe kryteria są w `../docs/GPU_ACCEPTANCE.md`.
