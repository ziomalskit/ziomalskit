# SECOND PASS AUDIT — po ponownym przejrzeniu paczki

Data: 2026-10-06

## Sprawdzenia integralności

- RC5 SHA256 oczekiwany: `10db9d1ac9a9e156739c1fe4400726f6712d17a47834378f69f06b6fe1eef4a4`
- RC5 SHA256 obliczony: `10db9d1ac9a9e156739c1fe4400726f6712d17a47834378f69f06b6fe1eef4a4`
- wynik: **PASS**
- lokalny current workflow: JSON parse **PASS**, top-level nodes = 161, links = 119
- Vast master 96GB: JSON parse **PASS**, top-level nodes = 159, links = 117
- deployment RC5 został rozpakowany do paczki i zawiera panel, provisioning, preflight, smoke, watchdog/control oraz oba workflowy.

## Audyt merytoryczny

Ponownie sprawdzono, czy handoff obejmuje:
- pierwotny problem identity/continuity z Wan2.2 i Stand-In — **TAK**;
- przejście na MiniMax H3 — **TAK**;
- Simply Advanced v1.5.4 jako bazę — **TAK**;
- stabilny native INT8 core — **TAK**;
- parametry First/Second Pass — **TAK**;
- Step 0 JoyCaption full + 3×3 overlapping tiles — **TAK**;
- Qwen3-VL missing-detail verifier — **TAK**;
- Gemma final anchor audit i Gemma Step 1/2 — **TAK**;
- Violet-Lotus Step 3/4 — **TAK**;
- 10Eros jako opcjonalne A/B — **TAK**;
- seed/cache policy — **TAK**;
- dokładny first-deploy input contract — **TAK**;
- batch 10 / auto 5 / review 5 i priorytety kolejki — **TAK**;
- dwa procesy ComfyUI 8188/8189 + panel 7860 — **TAK**;
- Basic Auth, atomic state, watchdog, recovery — **TAK**;
- live-only gates i zakaz płatnego renderu przed preflight/smoke — **TAK**;
- znane błędy: corrupt INT4 safetensors, missing Violet model, NVFP4/Mixed dead ends, 16GB unload hacks, seed wiring — **TAK**;
- ograniczenia źródeł i nie-materializowalne Project files — **TAK**.

## Rozbieżność lokalny vs 96GB master — celowa

Lokalny v8 ma explicit unload nodes 7384/7385. 96GB Vast master ich nie ma. To **nie jest brak w migracji**, tylko świadoma zmiana pod 96 GB.

## Wniosek

Paczka jest wystarczająca, aby nowy czat rozpoczął od aktualnego RC5 i rozumiał historię decyzji bez ponownego odtwarzania całej rozmowy. Jedyną nieredukowalną luką jest brak literalnego eksportu wszystkich wiadomości z czatu o tytule `Analiza poprzedniego czatu` oraz brak raw-byte access do części Project files; ich istotna treść została zrekonstruowana i opisana.
