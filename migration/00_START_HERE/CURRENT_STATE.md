# CURRENT STATE — source of truth dla kontynuacji

## 1. Cel produktu

Zbudować wygodny system MiniMax H3 Ref2VA, który:
- przyjmuje zestaw referencji i prompt użytkownika;
- automatycznie analizuje materiały referencyjne;
- tworzy kilka wariantów mocnych promptów H3;
- pozwala część kandydatów renderować automatycznie, a część zatwierdzać ręcznie z telefonu;
- renderuje na wynajętym GPU Vast.ai bez konieczności utrzymywania domowego serwera;
- zachowuje niezawodność kolejki i minimalizuje ryzyko podwójnego płatnego renderu.

## 2. Aktualny lokalny workflow

Plik: `CURRENT_REF2VA_INT8_CONVROT_JOYCAPTION_v8_3X3_TILES_QWEN_VERIFIER.json`.

Bazowy H3:
- diffusion model: `minimax_h3_ref2va_pruned_int8_convrot.safetensors`;
- text encoder: `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` (Blackwell);
- video VAE: `minimax_h3_video_vae_int8_convrot.safetensors`;
- audio VAE: `minimax_h3_audio_vae_fp32.safetensors`;
- latent upscaler: `minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors`;
- latent upscale: **0.8 MP**.

Render:
- First Pass: **20 steps**, `res_multistep`, scheduler `simple`, denoise `1.0`;
- Second Pass: **4 steps**, `er_sde`, scheduler `linear_quadratic`, denoise `0.30`;
- na wersji 96 GB usunięto explicit unload nodes i `force_unload=false`.

Domyślny First Pass LoRA stack w aktualnym mapowaniu:
- ON `HMBreastsV2.safetensors` strength 1.0;
- OFF turbo 8-step LoRA;
- ON `MysticXXX_MMH3-V4-ref2va.safetensors` strength 0.6;
- OFF `HMNSFW-AIO-V2.5.safetensors` 0.6;
- ON `movement_h3_lora_v1_500.safetensors` 0.5;
- OFF `MMH3-MPOV.safetensors` 0.6;
- OFF `H3_Combat_V2.safetensors` 0.6.
Second Pass LoRA stack jest pusty.

## 3. Autoprompter / analiza referencji

### Step 0 — anchor fact extraction
Primary anchor = **Picture 1**.

**0A JoyCaption**:
- pełnoobrazowy forensic caption;
- dodatkowo automatyczne **3×3 overlapping tiles** (~46% rozmiaru z overlapem);
- nacisk na identity/face, włosy, body shape, warstwy ubrań, wzory, tatuaże/znaki z lokalizacją, akcesoria, kadr i światło;
- ekspresja/emocja ze zdjęcia jest traktowana jako non-binding, chyba że user wyraźnie chce ją przenieść.

Model: `Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf` + matching mmproj.

**0B Qwen3-VL verifier**:
- `Qwen3VL-8B-Instruct-Q4_K_M.gguf` + `mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf`;
- szuka brakujących, źle zlokalizowanych i nadmiernie uproszczonych detali względem obrazu i raportów JoyCaption.

**0C final anchor audit**:
- Gemma Vision scala pełny caption + 9 tiles + Qwen audit w finalny techniczny report anchor.
- model: `Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.i1-Q6_K.gguf` + mmproj.

### Step 1–4
- Step 1 — Prompt Enhancement/Expansion: Gemma Vision;
- Step 2 — Reference Analysis / Reference Map: Gemma Vision + audited anchor evidence;
- Step 3 — Creative Director: `MN-Violet-Lotus-12B.Q5_K_M.gguf`;
- Step 4 — Final H3 Prompt Compiler: ten sam Violet-Lotus writer.

Typowe parametry z obecnego workflow:
- Step 1: 2048 tokens, temp 0.8, ctx 8192;
- Step 2: 2048 tokens, temp 0.8, ctx 8192;
- Step 3: 4096 tokens, temp 0.95, ctx 16384;
- Step 4: 3072 tokens, temp 0.80, ctx 16384.

Step 4 ma zwracać wyłącznie właściwy prompt H3 w sześciu sekcjach: `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`.

## 4. Polityka seedów / cache w deployment

RC5 rozdziela dwa rodzaje losowości:
- jeden `analysis_seed` na batch kandydatów, żeby Step 0–2 mogły się cache'ować;
- osobne seedy wariacji Step 3/4 dla kandydatów.

Przyjęta logika:
- Step 0 stabilny;
- Step 1 = `analysis_seed`;
- Step 2 = `analysis_seed + 1`;
- Step 3 = candidate `prompt_seed`;
- Step 4 = `prompt_seed + 1`.

To jest ważne — wcześniejszy błąd polegał na tym, że Step 3/4 miały lokalne widgety seed, podczas gdy faktycznie authoritative seed był podłączony upstream. RC2/RC5 naprawia controller upstream (node 4022).

## 5. Input contract na pierwszy deployment

Panel/backend przy pierwszym wdrożeniu zakłada:
- **dokładnie 6 obrazów**: Picture 1 + 5 support references;
- **0 albo 1 audio**;
- brak audio jest neutralizowany technicznym plikiem ciszy + reference disabled;
- wiele plików audio jest celowo wyłączone na pierwszy deploy.

API-map wejść:
- Picture 1 (primary anchor): node 3554;
- Picture 2–6: 4613, 4624, 4635, 4646, 4657;
- audio slots w workflow: 3944, 4734, 4749 (frontend na start obsługuje max 1);
- user prompt: node 2624 field `positive`;
- prompt override: node 2632 field `positive`;
- final prompt named value: node 1716 `final_prompt`.

## 6. Batch UX / kolejka

Docelowo batch = 10 kandydatów:
- #1–5 automatyczny render;
- #6–10 review przed renderem;
- generowanie promptów dla oczekujących batchy ma wyższy priorytet niż review generation;
- ręcznie zaakceptowany kandydat review trafia **przed** auto-rendery oczekujące w kolejce renderu;
- każdy candidate zachowuje własny prompt i przypisane referencje.

## 7. Vast.ai — aktualna architektura RC5

Target: **RTX PRO 6000 Blackwell 96 GB**.

Jedna instancja, ale dwa procesy ComfyUI:
- render worker: `127.0.0.1:8188`, `--highvram`;
- prompt worker: `127.0.0.1:8189`, DynamicVRAM z headroom ~6 GB;
- panel/control app: port **7860** — to jedyny port, który powinien być publicznie wystawiony.

Wspólne: models/input/output. Oddzielne: temp/user/sqlite dla workerów.

Bezpieczeństwo i trwałość:
- global Basic Auth, fail-closed;
- klucz API Vast wyłącznie po stronie serwera;
- STOP/DESTROY mają zabezpieczenia i typed confirmation;
- persistent volume trzeba ustawić **przed** provisioningiem;
- stan kolejki/control jest zapisywany atomowo;
- watchdog najpierw cancel/interrupt, a dopiero później restartuje tylko dotknięty worker;
- recovery sprawdza `/history` i `/queue` przed ponownym wysłaniem, żeby zmniejszać ryzyko podwójnego płatnego renderu.

Pinned:
- ComfyUI 0.38.0;
- comfy-cli 1.21.0;
- Linux llama.cpp b8840 CUDA dla LLM node;
- CUDA 13 preferowane, minimum planowane 12.8 dla tego runtime.

## 8. Co MUSI zostać sprawdzone na żywo przed płatnym renderem

RC5 jest zamknięty offline, ale pozostały live-only gates:
1. instalacja dokładnych custom nodes na wybranym obrazie Vast;
2. live UI→API conversion względem realnego `/object_info`;
3. fizyczna obecność wszystkich wymaganych modeli;
4. obserwacja, czy Step 0–2 rzeczywiście cache'uje się od candidate #2;
5. jednoczesne prompt/render: VRAM + throughput na RTX PRO 6000 96 GB;
6. zachowanie credentials i Local Volume na konkretnym offerze Vast.

**Nie uruchamiać płatnego renderu H3, dopóki `preflight.sh` i `smoke_test.sh` nie przejdą.**

## 9. Opcjonalne modele / branch

10Eros Hybrid Beta5 i jego wersje Visible Causality są zachowane jako tor A/B/reference-safe. Nie zmieniać defaultu native H3 INT8 bez świadomego testu jakościowego.

## 10. Referencyjna wydajność lokalna

Na RTX 5080 16 GB native H3 INT8 przy obecnym ciężkim workflowie obserwowany render 20 s był rzędu ~1200 s. Log telemetry z 2026-10-06 pokazuje GPU pod praktycznie stałym 98–100% SM load i framebuffer ~15.25 GB podczas pomiaru; to jest istotny punkt odniesienia przy benchmarku Vast, ale nie należy przenosić tego 1:1 na 96 GB GPU.
