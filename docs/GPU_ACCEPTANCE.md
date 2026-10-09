# Uruchomienie na GPU — acceptance po CPU/runtime PASS

Ten dokument opisuje **końcową, skonsolidowaną sesję GPU/Vast**. Zgodnie z
[ROADMAP.md](ROADMAP.md) najpierw kończymy feature set offline/CPU, wykonujemy pełną
regresję/audyt CPU i zamrażamy kompletne provenance finalnych modeli.
Dopiero po tych etapach wynajmujemy GPU i przechodzimy wszystkie poniższe gate'y
w jednej sesji. Profile to **H3 Full** i **10Eros Full**, z dokładną logiką
**v20 Heretic**. Manifest ma 17 pinned artefaktów; pełne metadata źródłowego
9B BF16 writera nadal blokuje gotowość. [Raport migracji](MODEL_MIGRATION_2026-10-09.md)
opisuje ten bloker. Nie wynajmuj GPU, dopóki nie zostanie zamknięty.

Bazą migracji jest merged PR #4, commit
`bfcc2bb22ff9691fda924fe17bd060a7e131c2e2`, CPU accepted: **358 testów**
oraz 16 HTTP smoke checks. Nowa migracja nie deklaruje GPU acceptance.

Nadal nie wykonano acceptance na realnym GPU dla aktualnego merge commita. Celem tej procedury jest przejście od zweryfikowanego runtime do pierwszego bezpiecznego deploymentu Vast.

## Docelowe środowisko

Planowany target:
- RTX PRO 6000 Blackwell 96 GB;
- Linux >=6.9;
- CUDA >=12.8, preferowane środowisko CUDA 13 zgodne z aktualnym installerem;
- jeden wybrany interpreter Python >=3.11;
- ComfyUI 0.38.0 i comfy-cli 1.21.0 zgodnie z runtime;
- publicznie tylko panel 7860;
- render ComfyUI 8188 i prompt ComfyUI 8189 dostępne lokalnie;
- trwały Vast Local Volume dla modeli, input/output i stanu wymagającego retencji.

## Gate 0 — przed wynajmem

Przed startem płatnej instancji:

0. Zakończ feature set, pełne `make setup`, `make test`, `make audit` na finalnym
   commicie oraz osobną decyzję o finalnym stosie modeli i ich zaufanych źródłach.

1. Upewnij się, że deployment pochodzi z aktualnego `main`, nie z `migration/`.
2. Zarezerwuj RTX PRO 6000 Blackwell 96 GB z wystarczającym dyskiem/RAM i Linuxem obsługującym wymagane pidfd process-group signalling.
3. Przygotuj osobny persistent volume i zanotuj faktyczną ścieżkę mountu. Sam katalog `/workspace` nie jest dowodem retencji.
4. Manifest musi być kompletny: źródłowe shardy/config/tokenizer 9B, dokładna
   rewizja i hash konwertera, BF16 recipe oraz exact sizes/SHA wszystkich
   pobieranych wag. Sprawdź dostęp i wymagane miejsce przed wynajmem; podstawowy
   stos zajmuje 219.55 GB, plus 9B source/output, LoRA i staging.
5. Przygotuj panel password i ewentualne dane Vast/Hugging Face poza repozytorium i logami.
6. Nie konfiguruj automatycznego DESTROY jako zabezpieczenia kosztowego przed przejściem live acceptance.

## Gate 1 — provisioning bez renderowania

Na świeżej instancji checkout aktualnego `main`, następnie:

```bash
cd h3
bash INSTALL_ON_VAST.sh
```

Jeżeli zachowanie danych jest wymagane, ustaw wcześniej:
- `H3_PERSISTENCE_MODE=volume`;
- `H3_PERSISTENT_ROOT=<rzeczywisty mount Local Volume>`.

Keep-data destroy pozostaje wyłączony, dopóki nie ma poprawnej `H3_PERSISTENT_VOLUME_PROOF` opisanej w `h3/README.md`.

Po instalacji nie przechodź jeszcze do generowania. Potwierdź:
- `nvidia-smi`;
- import torch i `torch.cuda.is_available()`;
- zgodność Python/CUDA/PyTorch;
- wersje ComfyUI/comfy-cli;
- komplet wymaganych custom nodes;
- komplet wymaganych modeli/LoRA;
- że panel ma Basic Auth;
- że 8188/8189 nie są publicznie wystawione.

Installer zostawia `H3_ALLOW_SUBMISSIONS=0`. Download token jest jednorazowym
`HF_TOKEN` odziedziczonym z procesu provisioningu, poza `runtime.env`, workerami,
stanem, logami i provenance. Potwierdź reuse zweryfikowanych wag po usunięciu
download tokena. Nie włączaj jeszcze automatycznej kolejki ani lifecycle.

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
audio może być pominięte. Włącz submissions dopiero świadomie dla tej kontrolowanej
sesji; normalny batch sam planuje pięć płatnych renderów.

Kryteria PASS:
- prompt worker kończy Step 0–4;
- wynikowy final H3 prompt jest poprawnie przechwycony;
- brak render POST do render workera;
- cache Step 0–2 działa przy kolejnych kandydatach tam, gdzie powinien;
- kandydat seed wpływa na Step 3/4;
- brak podwójnego autopromptera na render workerze;
- kolejka/persistence pozostają zdrowe.
- full/F16 JoyCaption + mmproj, verifier V4.5 F16 + mmproj oraz Gemma F16 + mmproj;
- 4B Heretic Q8_0 dla H3, wygenerowany z canonical 9B BF16 GGUF dla 10Eros;
- osobne Step 3/4 prompts, seeds, temperatury i outputs, bez vision mmproj/thinking;
- sześć sekcji i pełne pokrycie requested runtime, szczególnie 20 sekund;
- zmiana profilu nie powtarza Step 0–2, a restart Prompt Worker zmienia epoch;
- durable tekst po restarcie nie wymaga założenia, że modele/cache GPU przetrwały;
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
- VRAM, czas renderu i logi nie wskazują na OOM/restart loop.

**Historyczny risk:** Full/BF16 H3 był zgłaszany jako źródło czarnych klatek na
niektórych konfiguracjach ComfyUI/Blackwell. CPU nie dowodzi, że problem zniknął.
Obejrzyj wynik i zbadaj klatki, jasność oraz rzeczywisty ruch; zachowaj dowody.
Nie zastępuj Full modelem INT8 ani innym checkpointem w celu ukrycia niepowodzenia.

LoRA compatibility i optymalne strength wymagają osobnego porównania w tej samej
sesji. H3 zachowuje defaults 1.0/0.6/0.5; 10Eros zaczyna z opcjonalnymi LoRA OFF.
Samo znalezienie lub załadowanie pliku nie oznacza GPU validation.

Zapisz: GPU, sterownik, CUDA, torch, peak VRAM, czas renderu, resolution, duration, seed i SHA checkoutu.

## Gate 5 — overlap i recovery

Dopiero po pojedynczym renderze:

1. Zmierz conservative H3 i balanced 10Eros. Podczas H3 sampling cold Step 0–2
   ma czekać; warm/durable analysis może uruchomić mały writer tylko przy fresh
   measured headroom. 10Eros może dopuścić analysis z measured headroom.
   Conditioning/decoding/recovery muszą zamykać okno overlap.
2. Porównaj wall time z render-only baseline.
3. Sprawdź restart prompt/render service podczas bezpiecznych stanów kolejki.
4. Sprawdź recovery po restarcie panelu.
5. Potwierdź brak duplicate POST po persistence/restart boundary.
6. Sprawdź watchdog na disposable jobie z krótkim timeoutem.
7. Sprawdź prefetch (domyślnie `H3_PROMPT_PREFETCH=3`): jednocześnie najwyżej jeden
   render H3, Prompt Worker może działać równolegle, bufor przyszłych renderów jest
   ograniczony, #1–5 mają pierwszeństwo, #6–10 ostatecznie trafiają do Review Pool.
8. Sprawdź Diagnostics, Live Logs i OPEN MP4/timing na realnym layoucie. Terminal
   jest opcjonalny, domyślnie wyłączony; jeśli świadomie włączony, sprawdź go tylko
   przez zaufane/prywatne połączenie, wraz z disconnect/cleanup.
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

GPU acceptance można uznać za PASS dopiero po udokumentowanym przejściu Gate 1–6 bez:
- duplicate paid submissions;
- utraty queued/accepted jobs;
- orphaned worker processes;
- niekontrolowanego publicznego dostępu do ComfyUI;
- utraty danych przy deklarowanym keep-data;
- OOM/restart loop na docelowym workflow.

Do tego momentu status projektu brzmi:

**GPU/live acceptance pending; complete 9B source provenance blocks readiness.**
