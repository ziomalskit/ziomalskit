# Audyt gotowości H3 Full + 10Eros Full

Audyt rozpoczęto na `140d9c222ffb18017bbb8913223f537cd3d055f5`, w istniejącym
Draft PR #8 (`feat/dual-llm-profiles`). Baza pozostaje bez zmian. PR #5
`c6f033ab41a4456fd55558e652a563d8c31418d1` i PR #6
`84af26e26c0ec85a40efe712e5e32506c5a9af3f` są przodkami tego checkoutu.
Wszystkie trzy PR pozostają open/Draft/unmerged. Nie traktowano starego `main`
ani archiwum jako kompletnego wdrożenia. Aktualnym celem jest pełne Gate 1–6.

**Decyzja: NO-GO dla rozpoczęcia dziś pełnej, nieprzerwanej sesji produkcyjnej.**
Możliwy jest warunkowy start odizolowanego Gate 1A. Brakuje konkretnej oferty,
danych hosta i billingu, zatwierdzonego kosztorysu oraz
rzeczywistych dowodów 27B i wynikającego z nich zatwierdzonego SHA produkcji.
CPU nie może zastąpić tych dowodów. Bramka DavidAU jest celowa i pozostała aktywna.

[Runbook](GPU_PRODUCTION_SESSION.md) prowadzi od 11 GGUF przez review do
pełnej instalacji, prób prompt-only, H3 → 10Eros → H3 i końcowego cleanup.
[Inwentarz](PRODUCTION_MODEL_INVENTORY_2026-10-14.md) zawiera wszystkie 22
niezmienne źródła, ścieżki, rozmiary i pełne SHA. Dokumenty nie stanowią zgody
na wynajem ani zapisu wykonanego GPU acceptance.

## Co prześledzono

| Obszar | Kod i zależności | Wniosek CPU i granica dowodu |
| --- | --- | --- |
| Kernel i procesy | Operator Phase 1, service_ctl, process_identity, main._require_restart_group_control | Produkcja wymaga rzeczywistego `pidfd_send_signal(fd,0,None,4)`; nie ma osłabionego fallbacku. Runbook wymaga także Linux >=6.9. Floor 6.8 dotyczy tylko izolowanego Phase 1. Własny probe CPU zwrócił 0 na kernelu 6.18.44; nie dowodzi to hosta Vast. |
| Host i cgroups | observe/inspect, preflight, runtime_config | Blackwell CC 12.0 / 96 GB, CUDA 13.0 i driver wymagają rzeczywistej inspekcji. Preflight sprawdza torch/CUDA i catalog. Builder sam nie stanowi pełnego audytu cgroups. Runbook zapisuje membership, mount, widocznych przodków i affinity; wymaga dowodu providera dla ukrytych limitów oraz jawnego cap liczby build jobs. |
| Modele i pobieranie | models_manifest, select_artifacts, provision_models, model_downloads, artifacts, file_publication | 22 unikalne wpisy mają poprawny format rewizji, size i SHA. Wybór 11 odpowiada approval, runtime, profilom i dokładnej bramce DavidAU. Produkcyjna walidacja nadal odmawia startu. Pobieranie zachowuje izolację, full SHA, atomowe no-overwrite, fsync, source lock i podpisy cache. Nie pobierano wag ani świeżych HF metadata; sprawdzono zapisane poświadczenia repozytorium. |
| Full render | production_profiles, templates, adapter v20, native boundaries | H3 BF16 transformer/encoder, Full Beta5 10Eros, video VAE FP16, audio VAE FP32 i upscaler. Frozen controls oraz walidator rzeczywistych ścieżek visual/audio pozostają. Nie podstawiono INT8/Turbo. Zwolnienie około 51 GB encodera przed około 66 GB transformerem, czarne klatki i model switch to testy GPU. |
| LLM | production, writer_policy, native_completion, compiler | Wspólne JoyCaption/verifier/Gemma F16 vision; Mistral Q4_K_M dla H3 i Q6_K dla 10Eros z BF16 projector; writery 14B Q4_K_M / 27B Q5_K_M. Step 3 OFF: max 8192 / ctx 24576. Step 4 ON: H3 budget 6144 / max 14400 / ctx 32768; 10Eros 8192 / 16448 / 36864. Istnieją native receipts i final/timeline guards; load, vision, templates, EOS i VRAM pozostają niezmierzone. |
| Instalacja i ComfyUI | provision, fallback nodes, setup_linux_llama, install_owned_nodes, preflight, smoke | Przypięte Comfy 0.38.0, comfy-cli 1.21.0, llama/node. Gate 2 sprawdza realne object_info i cztery niesubmitujące grafy. Własny native package jest związany z manifestem. Registry dependencies wymagają także zapisania rozwiązanych wersji. Installer na końcu uruchamia oba Comfy i panel, z submissions OFF. |
| Panel i media | API/UI/auth/proxy/upload/public_job/diagnostics/logs | Dokładnie 6 refs i 0/1 audio; auth, upload bez podążania za linkami i bez overwrite, MP4/Range, queue/results i bounded logs mają testy CPU. Terminal jest niedostępny produkcyjnie. Rzeczywiste media, public ingress i browser UX wymagają Gate 4/5. |
| Kolejki i recovery | request_identity, persistence, _submit_workflow, execution journal, workers | Trwały UUID przed POST; timeout/bad ACK/503 oznacza uncertain, bez blind resubmit. Persistence failure i quarantine blokują dispatch. Manual identity i wykluczenie Prompt Worker pozostają. Autoprompt: 10 kandydatów = 5 auto + 5 review; manual: 1 job. Normalny batch nie służy do prompt-only. |
| Overlap | analysis_cache, gpu_runtime, phase admission, native leases | Jeden ciężki render z priorytetem. Cold Step 0–2 wyłączone podczas samplingu obu profili. Writer headroom 24576/40960 MiB; conditioning/decoding/recovery/unknown zamykają admission. Wspólny Step 0 i oddzielne Step 1/2 są związane z identity. Progi wymagają pomiaru KV/workspace na GPU. |
| Storage i lifecycle | persistent_storage_status, provider proof, service_ctl, guard/action API | Proof wiąże retained volume z instance, boot, mount, dev i inode. Protected paths muszą leżeć na tym samym trwałym mount. Token decyzji, persistence generation, stop/drain/destroy i pidfd ownership pozostają. Nie wykonano retencji ani provider start/STOP/DESTROY/reattach. |
| Koszt | Operator Phase 1 i panelowy cost hold | Phase 1 wymaga actual billing, niezmiennej oferty i deadlines. Panelowy guard używa świeżej estymaty compute od startu kontrolera; nie sumy faktury całej sesji. Zewnętrzny rejestr billingu i deadline obowiązują przez restarty oraz retencję. |

## Blokery i poprawki

1. **DavidAU:** wymagany pending `10eros-writer` blokuje normalny installer już
   przy model plan. Qwen27 ma przypięte źródło, lecz nie ma realnego acceptance.
   Dopiero dowody Gate 1A pozwalają na osobny reviewowany follow-up. Checkout G1
   i jego store pozostają do weryfikacji; G2 ma osobny zatwierdzony SHA. Manual
   render filtering nie zastępuje acceptance pełnego Autoprompt.
2. **Opcjonalne zewnętrzne LoRA:** po audycie elastyczności rozpoczętym na
   `ce1fc3f04e53840e07e5b58240226abb4c986604` MysticXXX i Movement są OFF w H3
   (strength 0.6/0.5 zachowane). Ich brak nie blokuje domyślnych renderów.
   Jawne włączenie wymaga plików i explicit enrollment; nie dowodzi on upstream
   provenance ani zgodności GPU. [Konfiguracja LoRA](LORA_CONFIGURATION.md).
3. **Host i finansowanie:** brak konkretnego zgodnego hosta, attachment,
   efektywnych cgroups, ceny, throughput, billingu, deadlines i uzgodnionego
   cleanup. Linux 6.8 z poprzedniej próby nie spełnia pełnej produkcji.
4. **GPU:** pozostają wszystkie rzeczywiste load/vision/EOS, catalog/torch,
   Full BF16 MP4, encoder release, VRAM/overlap, recovery i lifecycle gates.
   Są to testy acceptance, nie powód do osłabienia kodu offline.
5. **Naprawiono ponowną instalację:** niezależny test odtworzył odmowę drugiego
   `install_fallback_nodes.sh` po własnej poprawce Linux selektora z
   `setup_linux_llama.sh`. Teraz tolerowany jest wyłącznie dokładny unstaged
   patch z tego samego pinu. Obce, staged, untracked i linkowane zmiany nadal
   blokują instalację i są zachowywane. Bez resetowania lub kasowania checkoutu.
6. **Przygotowano prompt-only:** wcześniej istniał opis bez bezpiecznej komendy,
   podczas gdy normalny batch planuje pięć renderów. Nowy eksporter
   `prepare_prompt_acceptance.py` sprawdza strict manifest/files/native przed
   i po produkcyjnej konwersji i final validator. Zapisuje prywatny frozen
   request z jednym UUID; nie uruchamia workera, nie publikuje batcha i nigdy
   nie wysyła POST. Aktywny DavidAU blokuje eksport. Przyszły ręczny POST jest
   osobnym, monitorowanym krokiem runbooka, bez automatycznego retry.
7. **Poprawiono dokumentację:** usunięto założenie gotowego `main`, opt-in
   Terminal w produkcji i wymaganie MP4 już podczas prompt-only. Rozdzielono
   kernel Phase 1/produkcji, metadata/file SHA/GPU PASS, zakres cost guarda
   oraz zniszczenie compute i osobne usunięcie volume.

## Kosztorys

Pełne 22 pliki: **284661829208 B**, wymagane 17: **277909390584 B**.
Aktualny domyślny wybór to 19 (wymagane + HM/Bunny): **278009016136 B**.
Izolowane 11: **113395969792 B**. Dwa store plus największy staging dla
**domyślnego** wyboru: **457685473296 B**, przed środowiskami, buildami, cache,
mediami i external LoRA. Z `--include-optional` baseline wynosi 464338286368 B.
Plan 768 GiB + L zostawia 366948247536 B ponad domyślny baseline; to rezerwa,
nie dowód wolnego miejsca. Kontrolowana kopia 11 oszczędza ponowny transfer
113.4 GB, lecz nie lokalny odczyt/zapis, miejsce ani destination SHA.

Sam zewnętrzny transfer domyślnych wag **z reuse** to 278.009 GB; przy
100/250/500/1000 Mbit/s trwa co najmniej 6.18/2.47/1.24/0.62 h. Trzeba dodać
instalacje, build, SHA, próby modeli,
review, co najmniej 23 rendery (w tym warianty upscaler/drugi pass obu profili)
oraz retry/lifecycle. Przy 23 × 30 min sam render zajmuje 11.5 h.
Wariant 8–24 h wymaga przeliczenia konkretnej oferty:
przykładowe compute $2.50/h to $20/$30/$60 za 8/12/24 h, **plus** storage,
transfer, backup i retencja. To scenariusz planistyczny, nie cena rynkowa ani
benchmark. **$15/4h nie uzasadnia dziś GO pełnych Gate 1–6.** Szczegółowe
rachunki i warunki ponownej oceny budżetu podaje runbook.

## Walidacja i integralność

- `make setup`: PASS, przypięte CPU dependencies i pip check.
- Skupiony suite: **88 PASS**, zero failures/errors/skips; deployment, fallback,
  model acceptance, kernel/operator oraz nowy prompt exporter.
- Nowe regresje: 2 testy reinstalacji/odmowy edycji i 5 testów eksportera.
  Próba reinstall przed poprawką: 1 test / 1 oczekiwana porażka z komunikatem
  `Local changes preserved`. Po poprawce fallback suite: 7 PASS; exporter: 5 PASS.
- Pełne `make test` i `make audit` wymagają osobnego raportu finalnego SHA.
  Wyniki lokalne i CI, liczby testów i ewentualne timeouty zapisuje końcowy
  raport PR #8. Ten dokument nie deklaruje pełnego CPU PASS na podstawie
  focused suite ani wcześniejszego 952-testowego CI.
- `git diff --check`: PASS na przygotowanej zmianie.
- **124 chronione pliki**: migration/archive/workflows/production config/model
  provenance/native source/controller oraz service_ctl/process_identity są
  identyczne bajtowo z 140d9c2. Kod zmieniono wyłącznie w helperach scripts.
- v20: **3084071 B**, SHA256
  `276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`.
- Nie pobierano wag, nie uruchamiano GPU/inferencji, nie wysyłano realnego Comfy
  POST, nie wykonano Vast action/rental ani paid provider action. Niczego nie
  mergowano i nie zmieniono bazy PR.

Pozostają INFO obserwacje niezależnego review: adapter descriptor live reread,
natywne filesystem TOCTOU i inherited multi-field damaged legacy recovery.
Nie rozszerzano na nie patcha. Rozwiązywane podczas instalacji registry
zależności, nadzór billingu przez operatora i ręczny review/cutover są jawnymi
ograniczeniami powtarzalności i nadzoru; nie poświadczono ich jako przetestowanych live.
