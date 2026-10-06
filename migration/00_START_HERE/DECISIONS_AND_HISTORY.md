# DECISIONS AND HISTORY — chronologia projektu

## Faza A — Wan2.2: długie wideo i identity (8–13 września 2026)

Projekt zaczynał się jako automatyczne chainowanie kilku segmentów Wan2.2 I2V. Główna obserwacja: sam ostatni frame dobrze przenosi continuity, ale może pogarszać identity w kolejnych segmentach. Powstała zasada:

**selected LAST_GOOD_FRAME = continuity**, a **ORIGINAL FIRST IMAGE = identity authority**.

Do identity miał służyć Stand-In. Chainowanie segmentów i merge IMAGE batches przed Save Video już działały. Literalny last frame został zastąpiony pomysłem LAST_GOOD_FRAME (np. N-3/N-5/N-8), bo końcowa klatka bywa przejściowa, zasłonięta lub z gorszą twarzą.

Istniał native Wan2.2 14B I2V HIGH/LOW (FP8), Scanner LOW i eksperymenty z R1. Ta gałąź jest dziś **historyczna**, ale jej filozofia „anchor/reference authority vs continuity” wróciła później w H3.

## Faza B — wejście MiniMax H3 (19–20 września)

Do projektu trafiają oryginalne workflowy MiniMax H3 (`image-to-video`, `refmod`, auto-prompt) oraz pierwsze lokalne kombinacje. Głównym kierunkiem staje się H3 Ref2VA, bo lepiej pasuje do pracy z wieloma referencjami i audio.

## Faza C — Simply Advanced H3 jako baza (koniec września)

Bazą dużego workflowu zostaje `simplyAdvancedMinimax_v154.json`. Projekt zaczyna rozwijać nie tylko render, ale też automatyczne tworzenie promptu, ref analysis, latent upscale i drugi pass.

## Faza D — przebudowa autopromptera i stabilizacja 16 GB (3–4 października)

Powstały kolejne wersje Gemma/Violet-Lotus, dual-LLM, Second Pass diagnostics, unloady i rollbacki. Najważniejsze wnioski:

- nie ufać zmianie modelu/quantization tylko dlatego, że zmniejsza VRAM;
- eksperymenty NVFP4/Mixed INT4/8 dawały problemy jakościowe/techniczne, więc produkcyjny core wrócił do `minimax_h3_ref2va_pruned_int8_convrot.safetensors`;
- Second Pass ustabilizowano przy 4 steps / er_sde / linear_quadratic / denoise 0.30;
- na 16 GB pojawiały się unload hacks; dla 96 GB mastera zostały świadomie usunięte.

Writer finalnie rozdzielono:
- Gemma Vision = reference reasoning;
- Violet-Lotus = creative writer + final compiler.

## Faza E — forensic anchor pipeline (5 października)

Największa zmiana jakości autopromptera:
- JoyCaption analizuje cały primary anchor;
- Picture 1 jest automatycznie dzielone na 3×3 overlapping crops;
- każdy crop ma osobny forensic caption;
- Qwen3-VL 8B działa jako niezależny verifier brakujących detali;
- Gemma scala evidence w final audited anchor report;
- Step 2 traktuje ten raport jako główne źródło trwałych cech Picture 1;
- wspierające obrazy nie mogą po cichu nadpisywać anchor identity/appearance;
- transient expression/mood nie jest automatycznie transferowany.

W ten sposób stary problem „identity authority” z czasów Wan2.2 został rozwiązany na dużo bogatszym poziomie semantycznym.

Równolegle zachowano 10Eros Ref2VA/Hybrid Beta5 jako A/B i reference-safe eksperymenty.

## Faza F — migracja do Vast.ai + sterowanie mobilne (6 października)

Cel przesunął się z „jak zmieścić wszystko na RTX 5080 16 GB” na „jak uruchomić pełny system wygodnie i bezpiecznie na 96 GB GPU”.

Powstały kolejne Stage1–Stage5, provisioning, panel mobilny, Vast control, scheduler, watchdog, preflight/smoke oraz RC5. Finalny kierunek:
- native INT8 jako production default;
- RTX PRO 6000 96GB;
- panel z Fold 6;
- brak wymagania domowego serwera;
- prompt worker i render worker osobno;
- ścisła kontrola kolejki i recovery;
- żadnego płatnego renderu przed live preflight + smoke.
