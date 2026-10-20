# GPU acceptance — zakres prób i rzeczywiste dowody

**Pierwsza sesja prywatnej aplikacji:** [Personal First Run](PERSONAL_FIRST_RUN.md).
Linux ze zwykłymi pidfd i czytelnym `/proc` (`process_identity.py support`), RTX PRO6000 Blackwell96GB,
CUDA13.0, zweryfikowane cgroups/RAM/CPU, prywatny panel, dokładny v20 i Full wagi
pozostają wymagane. Wspieramy ephemeral instance disk i jeden fizyczny store
`COMFY_ROOT/models`. Local Volume jest potrzebny tylko dla świadomej retencji;
keep-data destroy bez rzeczywistego proof pozostaje niedostępny.

Domyślne19wag=278009016136B, bundle22=284661829208B,11GGUF to podzbiór tego
samego store. Każdy używany plik ma source/revision/size/SHA256. Nieużywany wpis
DavidAU9B został usunięty; Step3/4 10Eros używają Qwen3.5-27B Q5_K_M. Można
instalować zweryfikowane wagi przed inferencją, bez retirement lub bypassu
manifestu. GPU pozostaje pending. Rzeczywisty pierwszy minimalny test27B musi
się udać przed użyciem10Eros autoprompt; jego błąd zatrzymuje tę ścieżkę.

Plan Personal: zatwierdzona prawdziwa oferta <=$15/4h → readonly preflight →
11GGUF na wspólnym store/native build → minimalna rzeczywista próba27B → pełny
installer z reuse, dwa ComfyUI workery i panel → preflight/smoke → pojedynczy
krótki H3 Full i10Eros Full manual render → sprawdzenie/pobranie MP4 z telefonu →
ręczny provider DESTROY i potwierdzenie końca billingu. Brak oferty nie daje GO.
Nie wykonano tych prób na GPU w przygotowaniu offline.

[Pełny runbook Gate1–6](GPU_PRODUCTION_SESSION.md) oraz poniższe kryteria są
**opcjonalną diagnostyką** szerszego zakresu, bez obowiązkowych23renderów.
Nie przedstawiaj ograniczonej próby jako PASS pełnego overlap/recovery/lifecycle.
Nie wymagaj całej sesji dla każdej normalnej zmiany seed/duration/LoRA.
[Phase1](GPU_ACCEPTANCE_PHASE1.md) zachowuje szczegółowe native probes. Datowane
raporty migracyjne/acceptance opisują historię, nie aktualny blocker DavidAU.

## Gate 0–1 — przygotowanie i instalacja bez submissions

Użyj dokładnego zatwierdzonego SHA istniejącego Draft PR8, aktualnego CPU/CI,
czystego checkoutu i canonical v20 hash. Nie zakładaj, że kod znajduje się na
main lub w archiwalnym migration. Sprawdź rzeczywisty host i instance disk,
stawki/czas/transfer/rezerwy, SSH tunnel i prywatne inputs. Nie wynajmuj zasobów
w zadaniu offline. Instalator nie podmienia modeli Full na INT8/Turbo.

Dokładne komendy i checklista pierwszych15min są w Personal First Run. Zawsze
submissionsOFF podczas instalacji; po niej: integralność plików, wersje
ComfyUI/customnodes/native, Torch/CUDA, BasicAuth, brak publicznych8188/8189.
Wymagane pending/source mismatch nadal zatrzymuje provisioning przed pobraniem.
Jednorazowy tokenHF jest wyłącznie w procesie downloadu, nie runtime.env,
receiptach, stanie lub logach. Test pierwszej27B ma prywatne rzeczywiste wyniki;
nie nadaje globalnego GPU validated ani pełnego Step3/4/EOS PASS.

## Gate 2 — preflight i conversion smoke

Po umieszczeniu modeli:

```bash
bash <PANEL_ROOT>/scripts/preflight.sh
bash <PANEL_ROOT>/scripts/smoke_test.sh
```

Oba muszą zakończyć się PASS.

Dodatkowo sprawdź realne `/object_info` obu ComfyUI i potwierdź obecność klas używanych przez workflowy.

`smoke_test.sh` używa ścieżek walidacji/konwersji i nie powinien wysyłać płatnego renderu. Wynikowy API graph nie może zawierać klas należących wyłącznie do UI ani aktywować bypassowanych gałęzi.

Smoke materializuje wspólne v20 dla obu writerów i obu rendererów. Sprawdź
AJAnalysisText, AJConditioningBoundary, AJLateUNETLoader i AJVideoBoundary.
Conditioning obu sampling passes musi poprzedzać zwolnienie encodera i late
transformer load; nie może powstać cykl model → conditioning → model.

## Gate 3 — prompt-only acceptance

To ma być **oddzielny prompt-only test**, nie zwykłe utworzenie panelowego batcha.

Użyj oddzielnej prompt-only ścieżki v20 dla każdego profilu i sześciu obrazów;
audio może być pominięte. `prepare_prompt_acceptance.py` eksportuje zweryfikowany
request bez POST i bez batcha, z produkcyjnymi submissions OFF. Operator wykonuje
jeden świadomy prompt POST dopiero według runbooka, z własnym deadline/billingiem;
normalny batch sam planuje pięć płatnych renderów.

Kryteria PASS:
- prompt worker kończy Step 0–4;
- Step 0–3 wymagają natywnych EOS/no-truncation receipts, również dla vision;
  plain CLI output/exit0 nie jest dowodem ukończenia. Prywatny one-shot adapter
  używa tego samego przypiętego silnika i zachowuje parametry oraz GPU lease;
- wynikowy final H3 prompt jest poprawnie przechwycony;
- brak render POST do render workera;
- cache Step 0–2 działa przy kolejnych kandydatach tam, gdzie powinien;
- kandydat seed wpływa na Step 3/4;
- brak podwójnego autopromptera na render workerze;
- kolejka/persistence pozostają zdrowe.
- full/F16 JoyCaption + mmproj, verifier V4.5 F16 + mmproj oraz Gemma F16 + mmproj;
- Step 1/2: llmfan46 Mistral Small 3.2 24B Instruct 2506 Ultra Uncensored Heretic
  Q4_K_M dla H3 i Q6_K dla 10Eros, oba z projektorem
  `Mistral-Small-3.2-24B-Instruct-2506-mmproj-BF16.gguf` z tego samego immutable
  uploadu, zgodnym według wydawcy; potwierdź rzeczywisty load/vision;
- Step 3/4: Qwen3-14B Heretic Q4_K_M dla H3 i Qwen3.5-27B Heretic Q5_K_M dla 10Eros;
- osobne Step 3/4 prompts, seeds, temperatury i outputs, bez vision mmproj;
- Step 3 thinking OFF, total 8192/context 24576; Step 4 ON, natywny budget
  **H3 6144 / 10Eros 8192**, final 8192 + framing 64, total **14400 / 16448**,
  context **32768 / 36864**; zapisz native EOS/no truncation receipts i zmierz
  KV/workspace przy tych kontekstach bez obniżania progów VRAM na podstawie GGUF;
- reasoning nie dociera do final prompt; natywny output file i guarded final channel;
- sześć sekcji i pełne pokrycie requested runtime, szczególnie 20 sekund;
- requested duration, legal frames i effective duration muszą odpowiadać v20:
  dla request 20 s jest 481 klatek, effective timeline 20.040 s, dokładne
  481/24 ≈ 20.041667 s. Zapisz quantization delta i różnicę centisecond floor;
- Step 3 Timeline kończy się dokładnie na effective duration, bez luk/overlap;
  również raw plan 90 s musi zostać deterministycznie retimed przed Step 4;
- Step 4 cuts odpowiadają canonical Step 3 starts; Shot 1 nie ma timestamp;
- zmiana profilu współdzieli Step 0, ale rozdziela Step 1/2 według modelu,
  kwantyzacji i instrukcji; kandydaci #2–10 odzyskują tę samą analizę swojego profilu;
- restart Prompt Worker zmienia epoch, zachowując zweryfikowane teksty;
- durable tekst po restarcie nie wymaga założenia, że modele/cache GPU przetrwały;
- cache sprzed poprawki F2 pozostaje zapisany, ale nie jest źródłem zweryfikowanej
  analizy; nowe native/controller identity podlega dotychczasowej reconciliation;
- writer → Gemma compiler można porównać jako osobny acceptance benchmark,
  zachowując v20 instructions i bez dodatkowego selektora w normalnym UI.

Jeżeli ten gate nie przejdzie, nie uruchamiaj renderu.

## Gate 4 — pojedyncze rendery Full i model switch

Uruchom dokładnie **jeden** kontrolowany render, nie pełne pięć auto-renderów.

Potwierdź najpierw H3 Full, następnie pojedynczy 10Eros Full, potem H3 ponownie:
- `minimax_h3_ref2va_bf16.safetensors` oraz Full Beta5 10Eros, bez INT8/Turbo/W4A8;
- BF16 H3 encoder, FP16 video VAE, FP32 audio VAE i pinned latent upscaler;
- realne zwolnienie/offload około 51 GB encodera przed około 66 GB transformerem;
- brak równoczesnego heavy load H3 i 10Eros i brak ciągłego per-step streamingu;
- oczekiwane LoRA i ich strength;
- poprawne 6 image references i opcjonalne audio;
- finalny prompt z prompt workera trafia bez ponownego przepisywania;
- jeden remote submission;
- sukces widoczny w `/history`;
- rzeczywisty MP4 istnieje i odtwarza się przez proxy/panel;
- MP4 frame count/FPS/duration potwierdza legal frames; odróżnij request,
  timeline i frame duration oraz normalną quantization delta;
- VRAM, czas renderu i logi nie wskazują na OOM/restart loop.

**Historyczny risk:** Full/BF16 H3 był zgłaszany jako źródło czarnych klatek na
niektórych konfiguracjach ComfyUI/Blackwell. CPU nie dowodzi, że problem zniknął.
Obejrzyj wynik i zbadaj klatki, jasność oraz rzeczywisty ruch; zachowaj dowody.
Nie zastępuj Full modelem INT8 ani innym checkpointem w celu ukrycia niepowodzenia.

LoRA compatibility i optymalne strength wymagają osobnego porównania w tej samej
sesji. H3 domyślnie HM 1.0 ON, MysticXXX 0.6 OFF i Movement 0.5 OFF;
10Eros zaczyna z opcjonalnymi LoRA OFF. Nieużywane LoRA nie wymagają instalacji.
Zwykłe ustawienia nowych jobów nie wymagają powtórzenia niepowiązanych gate
acceptance; nowy adapter wymaga własnej kontroli zgodności/VRAM. Patrz
[zasady konfiguracji](LORA_CONFIGURATION.md).
Samo znalezienie lub załadowanie pliku nie oznacza GPU validation.

Zapisz: GPU, sterownik, CUDA, torch, peak VRAM, czas renderu, resolution, duration, seed i SHA checkoutu.

## Gate 5 — overlap i recovery

Dopiero po pojedynczym renderze:

1. Zmierz oba nowe writery. Step 0–2 czeka podczas samplingu obu profili.
   Warm/durable analysis dopuszcza Step 3/4 tylko przy fresh measured headroom.
   Sprawdź progi powyżej i poniżej granicy z rzeczywistym KV/context/workspace.
   Conditioning/decoding/recovery/unknown zamykają admission przed GPU pracą,
   czekają na reap aktywnego LLM i pauzują kolejne kroki. Nie zamrażaj procesu CUDA
   sygnałem STOP; musi zwolnić model przed decodingiem.
2. Porównaj wall time z render-only baseline.
3. Sprawdź restart prompt/render service podczas bezpiecznych stanów kolejki.
4. Sprawdź recovery po restarcie panelu.
5. Potwierdź brak duplicate POST po persistence/restart boundary.
6. Sprawdź watchdog na disposable jobie z krótkim timeoutem.
7. Sprawdź prefetch (domyślnie `H3_PROMPT_PREFETCH=3`): jednocześnie najwyżej jeden
   render H3, Prompt Worker może działać równolegle, bufor przyszłych renderów jest
   ograniczony, #1–5 mają pierwszeństwo, #6–10 ostatecznie trafiają do Review Pool.
8. Sprawdź Diagnostics, Live Logs i OPEN MP4/timing na realnym layoucie. Terminal
   pozostaje niedostępny w produkcji, także przy `H3_ENABLE_TERMINAL=1`.
9. Zmierz rzeczywiste peak VRAM i cache zachowanie pod presją GPU, cold/warm batch,
   OOM/restart i render speed. Dopiero te dowody pozwalają dostroić progi pamięci.

## Gate 6 — Vast lifecycle

Dopiero gdy persistent storage raportuje VERIFIED i rzeczywiście zawiera output/state:

1. STOP AFTER CURRENT.
2. Start tej samej instancji z Vast i sprawdzenie onstart/runtime.
3. STOP AFTER QUEUE.
4. Ponowne uruchomienie i kontrola kolejki/stanu.
5. DESTROY COMPUTE / KEEP DATA dopiero po ręcznym potwierdzeniu attachment proof i obecności danych na Local Volume.

Po każdym kroku sprawdź, że lifecycle control generation, queue persistence i ownership usług pozostają spójne.

## Kryterium końcowe GPU acceptance

Pełny zakres Gate1–6 można uznać za PASS po jego faktycznym wykonaniu bez:
- duplicate paid submissions;
- utraty queued/accepted jobs;
- orphaned worker processes;
- niekontrolowanego publicznego dostępu do ComfyUI;
- utraty danych przy deklarowanym keep-data;
- OOM/restart loop na docelowym workflow.

Do tego momentu status projektu brzmi:

**GPU/live acceptance pending. Verified weights are installable; real model/Full render behaviour remains to be measured.**
