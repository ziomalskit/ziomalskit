# Uruchomienie na GPU — acceptance po CPU/runtime PASS

Ten dokument opisuje **końcową, skonsolidowaną sesję GPU/Vast**. Zgodnie z
[ROADMAP.md](ROADMAP.md) najpierw kończymy feature set offline/CPU, wykonujemy pełną
regresję/audyt CPU, a następnie osobno wybieramy i zamrażamy finalne modele.
Dopiero po tych etapach wynajmujemy GPU i przechodzimy wszystkie poniższe gate'y
w jednej sesji. Aktualny manifest modeli jest nadal provisional; ten dokument nie
zamraża URL-i ani checksumów.

Na `main` po PR #1 wszystkie znane merge-blocking problemy z STEP 1–3 i cross-step zostały naprawione i niezależnie zweryfikowane. Baseline z 8 października 2026 przechodzi `make test` i `make audit` po **290 testów**, a `make test` dodatkowo 16 HTTP smoke checks.

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
4. Miej gotowe źródła i dokładne nazwy wymaganych wag/modeli. Nie zgaduj URL-i i nie akceptuj częściowych/uszkodzonych downloadów.
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

## Gate 2 — preflight i conversion smoke

Po umieszczeniu modeli:

```bash
bash <PANEL_ROOT>/scripts/preflight.sh
bash <PANEL_ROOT>/scripts/smoke_test.sh
```

Oba muszą zakończyć się PASS.

Dodatkowo sprawdź realne `/object_info` obu ComfyUI i potwierdź obecność klas używanych przez workflowy.

`smoke_test.sh` używa ścieżek walidacji/konwersji i nie powinien wysyłać płatnego renderu. Wynikowy API graph nie może zawierać klas należących wyłącznie do UI ani aktywować bypassowanych gałęzi.

## Gate 3 — prompt-only acceptance

To ma być **oddzielny prompt-only test**, nie zwykłe utworzenie panelowego batcha.

Użyj workflowu prompt-only i sześciu małych obrazów referencyjnych; audio może być pominięte.

Kryteria PASS:
- prompt worker kończy Step 0–4;
- wynikowy final H3 prompt jest poprawnie przechwycony;
- brak render POST do render workera;
- cache Step 0–2 działa przy kolejnych kandydatach tam, gdzie powinien;
- kandydat seed wpływa na Step 3/4;
- brak podwójnego autopromptera na render workerze;
- kolejka/persistence pozostają zdrowe.

Jeżeli ten gate nie przejdzie, nie uruchamiaj renderu.

## Gate 4 — jeden natywny render H3

Uruchom dokładnie **jeden** kontrolowany render, nie pełne pięć auto-renderów.

Potwierdź:
- native MiniMax H3 INT8;
- właściwy Qwen encoder i VAE;
- oczekiwane LoRA i ich strength;
- poprawne 6 image references i opcjonalne audio;
- finalny prompt z prompt workera trafia bez ponownego przepisywania;
- jeden remote submission;
- sukces widoczny w `/history`;
- rzeczywisty MP4 istnieje i odtwarza się przez proxy/panel;
- VRAM, czas renderu i logi nie wskazują na OOM/restart loop.

Zapisz: GPU, sterownik, CUDA, torch, peak VRAM, czas renderu, resolution, duration, seed i SHA checkoutu.

## Gate 5 — overlap i recovery

Dopiero po pojedynczym renderze:

1. Uruchom render i przygotowanie następnego prompt batcha równolegle.
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

**CPU/runtime accepted; GPU/live acceptance pending.**
