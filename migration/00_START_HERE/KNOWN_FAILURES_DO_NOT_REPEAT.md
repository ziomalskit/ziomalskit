# KNOWN FAILURES / DO NOT REPEAT

## 1. Uszkodzony INT4 text encoder

Błąd:
`ValueError: The safetensors header is too large`

Dotyczył:
`qwen3vl_32b_minimax_h3_int4_convrot.safetensors`

Wniosek: plik był corrupt/invalid. Nie traktować tego jako dowodu, że CLIPLoader/H3 ogólnie nie obsługuje INT4. Aktualny Blackwell text encoder to `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`.

## 2. Violet-Lotus „Model not found”

Powtarzał się błąd:
`Model not found: MN-Violet-Lotus-12B.Q5_K_M.gguf`

To był problem ścieżki/obecności modelu w LLM node, nie błąd architektury Step 3/4. Aktualny workflow nadal używa Violet-Lotus w Step 3 i Step 4.

## 3. NVFP4/Mixed model swaps

Eksperymenty ze zmianą core H3/quantization w celu oszczędności VRAM nie dały stabilnego jakościowo następcy produkcyjnego INT8. Z tego powodu powstały rollbacki, a aktualny default wrócił do native INT8 ConvRot.

## 4. Unload hacks były sprzętowo uwarunkowane

Na RTX 5080 16 GB potrzebowaliśmy agresywnego zarządzania VRAM. Nie przenosić w ciemno tych unload nodes do 96 GB mastera. `VAST_H3_MASTER_NATIVE_INT8_96GB.json` celowo usuwa nodes 7384 `UnloadCPUModels` i 7385 `UnloadAfterSampling`, a latent upscaler ma `force_unload=false`.

## 5. Seed wiring w Step 3/4

Nie zakładać, że seed widoczny lokalnie w LLMTextProcessor jest authoritative. W workflowie seedy są połączone. RC2 naprawił upstream seed controller, tak aby warianty faktycznie różniły Step 3/4 przy współdzieleniu Step 0–2.

## 6. ComfyUI .venv — historyczny Wan2.2

W starej instalacji Stand-In próba instalowania dependencies przy działającym ComfyUI doprowadziła do `WinError 5` na `onnxruntime_providers_shared.dll`, a `.venv\Scripts\python.exe` został wyzerowany. Poprawna naprawa była przez `uv venv --allow-existing`.

Nie kopiować ręcznie `standalone-env\python.exe` do `.venv\Scripts\python.exe` i nie usuwać całego `.venv` bez potrzeby.

## 7. Ścieżki Windows zmieniły się

Legacy handoff Wan2.2 używał `A:\ComfyUI\ComfyUI\ComfyUI`.
Nowsze logi H3 pokazują `A:\ComfyUI\ComfyUI (1)\ComfyUI`.

Nie zakładać ścieżek ze starego handoffu bez sprawdzenia aktualnej instalacji.

## 8. RC5 nie jest jeszcze dowodem live-deploy

Offline audit jest zamknięty, ale realny Vast host może ujawnić:
- import failure custom node;
- różnice `/object_info`;
- brak konkretnego modelu;
- inny kształt `/history`;
- brak cache Step 0–2;
- gorszy overlap VRAM/throughput;
- problemy z Local Volume/credentials.

Dlatego obowiązuje kolejność `preflight.sh` → `smoke_test.sh` → prompt-only → pojedynczy native render → overlap benchmark.
