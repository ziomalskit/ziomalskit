# GPU Acceptance Phase 1 — runbook operatora

Status przygotowania: **runbook READY do review; rozpoczęcie kosztów BLOCKED**,
dopóki operator nie zatwierdzi konkretnej oferty, limitów, retencji i zakresu.
Ten dokument nie jest zgodą na wynajem. Nie uruchomiono GPU, Vast, pobierania
rzeczywistych wag ani inferencji. PR #8 pozostaje Draft.

Punkt wyjścia: `bf802ea118db848e35714b259d0f9246d3956144`, gałąź
`feat/dual-llm-profiles`. Baza PR #6: `84af26e26c0ec85a40efe712e5e32506c5a9af3f`,
zależna od PR #5: `c6f033ab41a4456fd55558e652a563d8c31418d1`.
Przed sesją użyj nowego, reviewowanego HEAD podanego w PR #8, nie historycznego
SHA tego punktu wyjścia. Nie merge'uj ani nie aktualizuj branchy automatycznie.

Zakres: jedna RTX PRO 6000 Blackwell 96 GB, izolowany native llama.cpp,
11 zatwierdzonych GGUF, pojedyncze ręcznie uruchamiane próby. Bez ComfyUI,
panelu, kolejki, renderów i testów lifecycle. Produkcyjna bramka DavidAU
pozostaje wymagana. Jej wycofanie wymaga osobnej analizy rzeczywistych wyników
Qwen3.5-27B i jawnie zatwierdzonego follow-upu.

## 1. Oferta, środowisko i wymagania do zatwierdzenia

| Parametr | Wymaganie / co zapisać |
| --- | --- |
| GPU | Dokładnie jedna widoczna RTX PRO 6000 Blackwell 96 GB; zapisz edition, UUID, rzeczywiste `memory.total` i compute capability **12.0**. Ada 6000 nie spełnia wymagania. Nie podstawiaj innej karty. |
| Kernel hosta | Linux **>=6.8 wyłącznie dla izolowanego Phase 1**; `6.8.0-142-generic` spełnia ten próg, 6.7 jest blokowany. Pełny deployment nadal wymaga **>=6.9 i rzeczywistego feature-probe `PIDFD_SIGNAL_PROCESS_GROUP`**. `uname -r` dotyczy hosta współdzielonego z kontenerem; obraz Ubuntu nie zapewnia wersji kernela. |
| Driver / CUDA | Kanoniczny plan tej fazy: CUDA **13.0 devel**, NVIDIA driver **>=580.65.06** jako konserwatywny baseline 13.0 GA. Zapisać pełne `nvcc --version`, wersję drivera i digest obrazu. Dla późniejszego update toolkitu sprawdzić jego własne wymaganie drivera; helper sprawdza major/minor i baseline, nie poświadcza zgodności wszystkich patch releases. |
| Runtime | llama.cpp `60eeeb6082c1126bb8bc72902c83123cd056811b`, tag `b10472`; LLM node `65983de33681816f856db7e16767da906084c688`; build **CUDA arch 120**; receipts i SHA obu native binaries. Nie zmieniaj pinów. |
| Python / narzędzia | Linux x86_64, Python >=3.11; proponowany Python 3.12. Preinstalowane Git, CMake, `nvcc`, compiler, `flock`, `findmnt`, GNU `timeout`, SSH i opcjonalnie `rsync`. Izolowane dwa venv. Installer nie instaluje apt ani Vast CLI. |
| RAM / CPU | Zapisać faktycznie przydzielone RAM/vCPU i cgroup limits, nie wyłącznie zasoby całego hosta. **Propozycja do akceptacji: 128 GiB RAM i 8 vCPU**, nie zmierzony minimalny próg. Builder używa najwyżej 8 jobs. Operator wpisuje własne zatwierdzone minimum dostępnego RAM/CPU; brak wartości blokuje helper. KV/workspace/page cache wymagają pomiaru. |
| Model disk | 11 plików: **113395969792 B**. Dla wszystkich brakujących: minimum model/download staging **132795578176 B**, w tym największy staging **19399608384 B**. To dysk, nie pełen koszt VRAM. |
| Pozostały disk | Dodać jawnie zatwierdzone rezerwy build/venv, prywatnych wejść, receiptów, logów i transfer staging. `build_reserve_bytes` oraz `evidence_reserve_bytes` pozostają puste. Propozycja pojemności 256 GiB dotyczy jednoplikiowego stagingu; kopiowanie całego zestawu do drugiego katalogu wymaga dodatkowych **113395969792 B**, więc ta propozycja może nie wystarczyć. |
| Volume | Prawdziwy, oddzielny Vast **Local Volume** na tej samej machine co docelowy GPU, Docker instance; zapisać machine/volume/instance IDs, attachment, mount path, source, fsroot, filesystem oraz `[st_dev, st_ino]`. `/data` jest przykładem, nie dowodem retencji. |
| Sieć | Zapisać rzeczywiste download/upload throughput i stawki obu kierunków. TLS do HF i niezbędnych pinned Git/package sources, SSH przez zaufaną ścieżkę. Nie wystawiać ports 7860/8188/8189 ani probe servera publicznie. |
| Prywatność | Zaufany host/dostawca, prywatne SSH, katalogi 0700 i pliki 0600. Host może mieć dostęp do danych na swoim sprzęcie. Prywatne prompt/reference/audio nie trafiają do publicznego PR ani logów. |
| Kontrakt / billing | Konkretna oferta, machine ID, data wygaśnięcia kontraktu, cena i rozbicie kosztów, saldo, ustawienia auto-top-up, bieżące wydatki, uzgodniona retencja oraz bezpieczny plan ręcznego STOP. Żadnej ceny nie wpisano za operatora. |

[NVIDIA compute capability](https://developer.nvidia.com/cuda/gpus) potwierdza
12.0 dla rodziny RTX PRO 6000 Blackwell.
[CUDA 13.0 GA release notes](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/index.html)
podają driver 580.65.06 i NVCC 13.0.48. `nvidia-smi` pokazuje maksymalną CUDA
obsługiwaną przez driver; nie zastępuje kontroli zainstalowanego `nvcc`.
CUDA 12.8 jest obsługiwaną przez istniejący builder alternatywą, ale zmiana
kanonicznego planu 13.0 wymaga osobnego review, właściwego drivera i spójnych
receiptów. Nie stosuj automatycznego fallbacku.

### Kernel: audyt izolowanej ścieżki i granica produkcji

Audyt na bazie HEAD `f54cbe359791c4a755ee8c92f7a6d586fdecd11c`
prześledził cały Phase 1 oraz źródła llama.cpp
`60eeeb6082c1126bb8bc72902c83123cd056811b` i selektora LLM node
`65983de33681816f856db7e16767da906084c688`:

| Etap / wywołania | Zależność od kernela |
| --- | --- |
| A, D i monitoring B/C: `gpu_acceptance_operator.py` → `model_acceptance`, `observe`, `inspect`, `watch` | Odczyt konfiguracji, `/proc`, cgroups, `findmnt`, `nvidia-smi`, `nvcc`, lokalnych portów i plików. Monitor tylko obserwuje; nie sygnalizuje procesów i nie steruje usługami ani Vast. |
| B: `INSTALL_MODEL_ACCEPTANCE.sh` → `provision_model_acceptance.sh` | Osobne root/venv, `flock`, source lock i izolowana instalacja. Nie wywołuje instalatora produkcyjnego, `service_ctl`, panelu, ComfyUI ani onstart. |
| Native build: `prepare_native_node` → `setup_linux_llama.sh` → `python_env`, `runtime_config`, `patch_llama_binary`, CMake, `install_llama`, `llama_runtime` | Pinned Git sources, CUDA arch 120, kontrola receiptów/SHA oraz `--version`. Publikacja używa `renameat2`; ten syscall istnieje od Linux 3.15. Brak wywołań pidfd group-control. Import `sync_directory` z `deployment` nie uruchamia deploymentu. |
| Download / verify: `model_acceptance` → `model_downloads` → `artifacts`, `file_publication`, HF SDK | `openat`/NOFOLLOW, `flock`, pełne SHA256, `fsync`, `renameat2(RENAME_NOREPLACE)` i bezpieczny reuse. To nie wymaga 6.9; brak wsparcia syscallu/filesystemu nadal blokuje publikację, bez fallbacku. |
| C: ręczny `llama-cli` lub jeden loopback `llama-server` + `curl`; `check_model_completion` i `temporal` | Przypięty native engine korzysta z normalnych POSIX signals, wątków, plików/socketów i CUDA. Checker ładuje parser `compiler.py` bez ComfyUI i wywołuje wyłącznie walidację receiptów/final/timeline. Nie uruchamia produkcyjnego adaptera procesów, GPU lease, workerów ani restartów. |

Nie znaleziono zależności tej ścieżki od Linux 6.9. Dlatego dopuszczamy
**6.8 jako konserwatywny floor Phase 1**, przy zachowaniu wszystkich pozostałych
bramek. Nie rozszerzamy zgody na starsze kernele. Wynik audytu kodu i testy CPU
nie poświadczają działającego drivera ani modelu na konkretnym hoście:
rzeczywiste build/load/vision/EOS/VRAM dla 11 zatwierdzonych plików pozostają
osobnymi GPU gates. CUDA 13.0 i driver 595.91.07 przekraczają obecny baseline,
ale same wersje nie dowodzą zgodności hosta i jego drivera.

Pełny deployment według [GPU_ACCEPTANCE.md](GPU_ACCEPTANCE.md) i
[h3/README.md](../h3/README.md) nadal wymaga **Linux >=6.9** oraz działającej
flagi `PIDFD_SIGNAL_PROCESS_GROUP` (4). Produkcyjne
`h3/app/main.py::_require_restart_group_control()` i
`h3/scripts/process_identity.py::require_group_control()` wykonują rzeczywisty
`pidfd_send_signal(..., 0, ..., 4)`; sama deklaracja wersji nie wystarcza.
Ich kod, sterowanie lifecycle i bramka DavidAU nie zmieniają się. Akceptacja
6.8 w Phase 1 nie zezwala na uruchomienie produkcji na tym hoście.

## 2. Czy można przygotować wagi bez drogiego GPU?

Potwierdzone w [Vast Volumes](https://docs.vast.ai/guides/instances/storage/volumes):
Local Volume przeżywa usunięcie compute, jest przywiązany do jednego fizycznego
hosta i nie można podłączyć go do innego hosta; obecnie dotyczy Docker, nie VM.
Istnieją copy/cloud-storage ścieżki do volume. Rozmiaru volume nie można zwiększyć
po utworzeniu. Nie wybieraj przypadkowego taniego hosta w oczekiwaniu, że jego
volume później dołączysz do Blackwella.

Możliwe warianty, **każdy z osobno zatwierdzonym kosztem i transferem**:

| Wariant | Co potwierdzono | Co pozostaje do sprawdzenia przed kosztem |
| --- | --- | --- |
| Pobranie w posiadanym lokalnym/CPU środowisku | Curated `--prepare` / `--download` nie potrzebują CUDA ani GPU; używają exact sources/SHA. | Dostępny dysk, sieć, licencje/dostęp do repo, HF token, koszt transferu do docelowego volume i dodatkowy staging. Nie uruchamiać tu GPU bootstrapu. |
| Zewnętrzny CPU/storage → cloud storage → docelowy Local Volume | Vast opisuje cloud copy do volume i kopiowanie między volumes. | Dostępność konkretnego hosta/volume, połączenie storage, stawki obu stron, wznowienie i zachowanie permissions. Nie przetestowano tej ścieżki w projekcie. Finalne pełne hashe sprawdzić na destination. |
| Tańszy compute na tym samym fizycznym hostcie | Ograniczenie attachment do tej samej machine jest potwierdzone. | **Nie potwierdzono dostępności CPU-only/tańszej oferty na wybranym hostcie.** Wymaga rzeczywistej oferty i gwarancji możliwości późniejszego wynajmu właściwej karty. |
| Download podczas pierwszego GPU rental | Najprostsza ścieżka istniejącego `--install`. | GPU nalicza koszt podczas downloadu i buildu. Osobno zatwierdzić download deadline i wariant STOP przy zbyt wolnej sieci. |

[Data Movement](https://docs.vast.ai/guides/instances/storage/data-movement)
opisuje także transfery do stopped instances z ograniczeniami oraz koszty
między hostami. Dostępności, czasu i bezpłatności konkretnego transferu nie
założono. Porównaj **całkowite** koszty, a nie tylko USD/h GPU.

Po osobnej zgodzie, w posiadanym CPU środowisku, z checkoutem zatwierdzonego SHA:

```bash
export AJ_ACCEPTANCE_ROOT=/actual/private/aj-native-model-acceptance
python3 h3/scripts/model_acceptance.py --root "$AJ_ACCEPTANCE_ROOT" --plan
python3 h3/scripts/model_acceptance.py --root "$AJ_ACCEPTANCE_ROOT" --prepare
python3 -m venv "$AJ_ACCEPTANCE_ROOT/.aj-download-venv"
"$AJ_ACCEPTANCE_ROOT/.aj-download-venv/bin/python" -m pip install -r h3/download-requirements.txt
# HF_TOKEN dostarcz jednorazowo przez zaufaną konfigurację procesu, bez argv/history.
"$AJ_ACCEPTANCE_ROOT/.aj-download-venv/bin/python" h3/scripts/model_acceptance.py \
  --root "$AJ_ACCEPTANCE_ROOT" --download
unset HF_TOKEN HF_HUB_TOKEN HUGGING_FACE_HUB_TOKEN
```

Nie przenoś markerów root, native buildów, venv ani receiptów z obcego hosta
bez ponownej weryfikacji. Marker zawiera absolutny root; file signatures są
lokalne. Na destination najpierw przygotuj jego własny store po kontroli mountu.
Kopiuj tylko 11 zatwierdzonych GGUF do tego odizolowanego store; nigdy do produkcji.
Jeżeli używasz rsync, generuj `--files-from` z `model_acceptance.load_selection()`
(`destination` każdego zatwierdzonego wpisu), stosuj `--ignore-existing` i
`--partial-dir=.transfer`, bez `--inplace`, bez kopiowania symlinków i bez overwrite.
Transfer ma być zakończony przed bootstrapem. Istniejących błędnych GGUF nie
naprawiaj w miejscu; STOP i review ich pochodzenia. `--install` rehashuje poprawne
istniejące bytes offline i pobiera tylko brakujące zatwierdzone pliki.
Resumable staging SDK i pełne sprawdzenia destination pozostają obowiązkowe.

## 3. Budżet — formularz i rachunek przed pierwszym kosztem

Skopiuj `h3/config/gpu_acceptance_session.example.json` poza repo do prywatnego
katalogu. Wszystkie limity, ceny, provider IDs i rzeczywiste mount facts mają
**null**, a approvals **false**. Zero oznacza potwierdzoną zerową stawkę, nie
brak informacji. Nie wpisuj sekretów ani całego eksportu konta/API.

Wypełnij co najmniej:

| Limit operatora | Wartość do zatwierdzenia |
| --- | --- |
| Maksymalna cena środowiska USD/h | `limits.max_instance_usd_per_hour = null`; suma compute + instance disk + volume podczas działania |
| Maksymalny całkowity budżet USD, wraz z transferem/retencją | `limits.max_total_usd = null` |
| Maksymalny czas pierwszej sesji, od rozpoczęcia naliczania | `limits.max_session_seconds = null` |
| Maksymalny preflight / review | `limits.max_preflight_seconds = null`, `limits.max_review_seconds = null` |
| Maksymalny download time | `limits.max_download_seconds = null` |
| Maksymalny build time | `limits.max_build_seconds = null` |
| Maksymalny czas jednej próby modelu, w tym startup/load | `limits.max_probe_seconds = null` |
| Operacyjny floor wolnego dysku | `limits.min_free_bytes = null`; nie zastępuje wyliczonego minimum models/staging/build/evidence |
| Polling i odstęp między aktualizacjami rzeczywistego billingu | `limits.poll_seconds`, `billing_max_age_seconds = null` |
| TERM → KILL grace dla własnej próby | `limits.shutdown_grace_seconds = null` |
| Dalsza retencja po sesji | `quote.retention_hours = null` i jawne stawki volume/stopped disk |

Cena jest do wpisania z **konkretnej aktualnej oferty i jej rozbicia**.
`quote.gpu_usd_per_hour` jest stawką compute, a stawki disk/volume są już
całkowitym USD/h dla zatwierdzonej pojemności. Nie wpisuj całego combined rate
ponownie jako compute. `quote.reference` wskazuje prywatny zapis oferty;
`valid_until_utc` oznacza zatwierdzony okres obowiązywania ceny/kontraktu.

Program liczy konserwatywny ceiling:

```text
H = max_session_seconds / 3600
R = retention_hours
USD = H * (GPU/h + instance_disk/h + volume/h)
    + R * (stopped_instance_disk/h + volume/h)
    + download_bytes_cap / 1e9 * ingress_USD/GB
    + upload_bytes_cap / 1e9 * egress_USD/GB
    + external_preparation_USD
```

GB w tej formule oznacza **10^9 bytes**. Jeżeli oferta podaje GiB lub USD/GB/h,
najpierw jawnie przelicz stawki i zapisz jednostki w prywatnym quote receipt.
Download/upload caps obejmują model bytes, retries, code/image/dependencies,
backup oraz transport między storage; nie tylko szczęśliwy pojedynczy download.
External preparation obejmuje osobno policzone CPU, storage i opłaty przed GPU
session. Retention hours dotyczą późniejszego zatrzymanego compute/volume.
Zarezerwuj także rzeczywisty czas instalacji, pełnego rehashu i wszystkich prób.
Suma planowanych B i prób C musi zmieścić się w session deadline. Brak informacji
o stawce lub potrzebnym czasie oznacza BLOCKED, nie automatyczne zero.

Dolne oszacowanie czasu transferu dla brakujących `B` bytes przy faktycznie
zmierzonej przepustowości `M` Mbit/s: `T >= 8 * B / (M * 1e6)`. To nie prognoza
z gwarancją: dolicz retries, współdzieloną sieć, pełne hashowanie i disk throughput.
Oszacuj zarówno wariant z GPU podczas transferu, jak i osobne preparation/storage.

[Vast Pricing](https://docs.vast.ai/guides/instances/pricing) opisuje compute,
storage i bandwidth jako oddzielne składniki. Zatrzymanie lokalnego procesu
nie zatrzymuje billing instancji. Provider STOP także pozostawia storage billing.
Nie używaj depletion salda jako kill switch ani destrukcyjnego automatu kosztowego.

## 4. Etapy, GO/STOP, czas i dowody

| Etap | GO | Limit i koszt | Co widać / dowody | STOP / przerwanie / resume |
| --- | --- | --- | --- | --- |
| A1: offline source/quote plan | Reviewowany SHA, zamknięta lista 11 źródeł, znane ceny/limity/retencja i kandydat machine/template. | Brak rental/download/inference; kosztorys z operator rates. Review ograniczony zatwierdzonym `max_preflight_seconds`; brak limitu blokuje start sesji. | Manifest/native pins, exact bytes/SHA, source plan, wszystkie brakujące pola i ceiling. Fakty przyszłego mountu pozostają NOT RUN. | Nowszy HEAD, pending wybrany model, brak stawki/limitu, niezgodny storage plan: nie ponosić kosztu. Plan nie zapisuje środowiska. |
| A2: readonly inspection na zatwierdzonym środowisku | **Osobna wcześniejsza zgoda na pierwszy koszt**; actual attachment/hardware facts uzupełnione z dwóch niezależnych źródeł. | Rental już może naliczać opłatę. Inspection nie tworzy nowej instancji. Obowiązuje `max_preflight_seconds` oraz cały session deadline. | Pinned checkout, `findmnt`, disk, GPU/driver/nvcc/cgroups, brak services/GPU work; PREFLIGHT_READY wyłącznie dla tych kontroli. | Błędny GPU/CUDA/kernel, mount, brak miejsca albo obcy proces: B/C zablokowane; ręczny provider STOP po zabezpieczeniu danych. Nie wymyślać mount proof. |
| B: isolated prepare/install | A2, `prepare_models=true`, aktualny koszt, zatwierdzone build/download rezerwy i deadlines. | `max_build_seconds + max_download_seconds`, w ramach session budget; GPU może kosztować przez cały transfer. | Build receipts, pełne 11 size/SHA, `gpu_acceptance=not_run`, submissions OFF. | Checksum/source drift, utrata mountu, niedostatek disk, deadline/budget: Ctrl-C/TERM własnego installera, sprawdzić zakończenie dzieci. Resume tym samym store i source lock po ponownym planie; zachować staging. |
| C1–C4: pojedyncze load/minimal text/vision | B kompletne i zweryfikowane, `model_probes=true`, GPU wolny, aktualny billing, wybrana jedna konkretna próba. | Każda `max_probe_seconds`, plus cały session deadline. Token/context ustawienia komendy podlegają review operatora. | Model/SHA, dokładny argv, load/perf/kv logs, GPU samples, rzeczywisty input, return code. Sam CLI output nie poświadcza naturalnego EOS ani pełnego acceptance. | OOM, brak GPU offload, wrong projector, obcy process, mount/budget/deadline: przerwać własną próbę, odnotować FAIL/NOT RUN. Nie zmieniać quant/modelu ani progów dla PASS. |
| C5–C7: Step 3/4 obu profili | Rzeczywiste, prywatne inputy wyprowadzone z v20 i 6 refs; zatwierdzone duration/seed; pełne profile budgets; poprawny canonical Step 3. | Jedna foreground native próba na raz, dokładnie jeden request; limity jw. Większy reasoning może wymagać dłuższej zatwierdzonej próby. | Native JSON, reasoning/final/EOS/finish/truncation, 6 sections, temporal validation, usage/timings/KV/VRAM. | Puste/ucięte/unclosed reasoning, EOS bez final, length/context limit, błędny timeline: FAIL. Nie przycinać odpowiedzi i nie łagodzić parsera. |
| D: review i zamknięcie procedury | Spójne prywatne receipts i stan każdej próby, verified retained mount, świadomy operator. | `max_review_seconds` i session deadline; aktualny billing i dalszy koszt retencji. Dalszy offline review po STOP compute. | PASS/FAIL/NOT RUN per case; hash inventory/backup; końcowy observed invoice i provider state. | Zakończyć własne native procesy. Bez uruchamiania ComfyUI/panelu/renderu/lifecycle testów. Żaden automat nie retire'uje DavidAU ani nie DESTROY'uje compute/volume. |

GO z helpera nie jest zgodą na wydatek ani GPU acceptance. A1 nie może dowieść
rzeczywistego GPU/mountu przed uzyskaniem dostępu: operator zatwierdza **warunkowy,
ograniczony koszt** i obowiązek STOP, gdy A2 zawiedzie. Nie tworzymy kolejnej
zależności kołowej wymagającej fałszywych st_dev/inode przed rental.

## 5. A — komendy tylko do odczytu i actual mount

Na lokalnym checkout:

```bash
git status --short
git rev-parse HEAD
git log -3 --oneline
git diff --check
# REF_SHA wpisz z reviewowanego PR #8; nie rób force-push/merge.
test "$(git rev-parse HEAD)" = "$REF_SHA"
H3_MODEL_ACCEPTANCE_ROOT=/actual/retained/mount/aj-native-model-acceptance \
  bash h3/INSTALL_MODEL_ACCEPTANCE.sh --plan
python3 h3/scripts/gpu_acceptance_operator.py \
  --session /private/operator-session.json --phase A
```

Niekompletny formularz zwraca exit 1/BLOCKED i nazwy brakujących pól; nie
uzupełnia ich. Jeżeli ceny/limity są wypełnione, może pokazać cost ceiling mimo
brakujących actual mount facts; pozostaje BLOCKED dla environment GO.
Source plan na lokalnym komputerze raportuje **lokalny** disk, nie przyszły host.
Sprawdź PR #5/#6/#8, HEAD, Draft status i komplet CI w GitHub przed wydatkiem.

Po wcześniejszym zatwierdzeniu konkretnego kosztu i uzyskaniu środowiska:

```bash
set -euo pipefail
uname -r
python3 --version
nvidia-smi --query-gpu=uuid,name,driver_version,memory.total,compute_cap --format=csv
nvcc --version
findmnt --json --target /actual/retained/mount --output TARGET,SOURCE,FSTYPE,OPTIONS,FSROOT
stat -c '%d %i' /actual/retained/mount
stat -c '%d %i' /
df -B1 /actual/retained/mount
cat /sys/fs/cgroup/memory.max
cat /sys/fs/cgroup/cpu.max
ss -ltnp
nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv
```

Volume proof ma dwa elementy: (1) operator niezależnie sprawdza w provider UI
volume/machine/instance/attachment i retencję po zakończeniu compute; (2) local
mount source/fsroot/fstype/rw oraz device/inode zgadzają się z tym attachmentem
i są oddzielne od container root. Dopiero wtedy wpisuje facts i
`provider_attachment=true`. Nazwa katalogu, ext4 lub same st_dev/inode nie są
provider attestation. Nie twórz automatycznie produkcyjnego
`H3_PERSISTENT_VOLUME_PROOF`; jego dotychczasowy kontrakt jest niezmieniony.

Sprawdź aktualny onstart/template: nie ma startu AJ/ComfyUI/panelu/workerów,
queued jobs ani restart automatu. Eksport nowych flag nie wyłącza istniejącego
obcego workera. Brak procesu GPU i brak listeners 7860/8188/8189 to konieczne
lokalne kontrole; niewidoczny/niepewny stan oznacza STOP.

```bash
export H3_ALLOW_SUBMISSIONS=0 H3_ALLOW_DIAGNOSTIC_SUBMISSIONS=0 H3_ENABLE_TERMINAL=0
export VAST_CLI=/usr/bin/false SERVICE_CTL=/usr/bin/false
unset VAST_API_KEY
export AJ_SESSION=/private/operator-session.json
AJ_PREFLIGHT_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["max_preflight_seconds"])')
AJ_GRACE_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["shutdown_grace_seconds"])')
test "$AJ_PREFLIGHT_SECONDS" != None
test "$AJ_GRACE_SECONDS" != None
timeout --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_PREFLIGHT_SECONDS}s" \
  python3 h3/scripts/gpu_acceptance_operator.py \
  --session /private/operator-session.json --phase A --inspect
```

Helper nie kontaktuje się z Vast, nie pobiera models, nie instaluje packages,
nie zapisuje production env/onstart/state, nie uruchamia CLI/servera do inferencji.
Diagnostic children otrzymują tylko PATH/LANG/LC_ALL, bez provider credentials.
Nie ma `--run`, `--rent` ani `--allow-pending`.

## 6. B — zatwierdzone przygotowanie i pomiary podczas downloadu

Operator uzupełnia `billing.session_started_utc` od początku rzeczywistego
naliczania, `observed_at_utc` i `observed_total_usd` z bieżącego provider billingu.
UTC ma pełny ISO timestamp z `Z`. Stan invoice może być opóźniony; monitor
dodaje koszt czasu i pełne rezerwy transferu/retencji, więc może konserwatywnie
liczyć część opłat ponownie. To nie jest automatyczny odczyt API ani dokładna
faktura. Nie przepisuj zamiast actual billingu oszacowania monitora.

Przed jedynym zaakceptowanym `--install`, po A2 oraz `prepare_models=true`:

```bash
set -euo pipefail
umask 077
export AJ_SESSION=/private/operator-session.json
AJ_BUILD_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["max_build_seconds"])')
AJ_DOWNLOAD_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["max_download_seconds"])')
AJ_GRACE_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["shutdown_grace_seconds"])')
test "$AJ_BUILD_SECONDS" != None
test "$AJ_DOWNLOAD_SECONDS" != None
test "$AJ_GRACE_SECONDS" != None
AJ_INSTALL_SECONDS=$((AJ_BUILD_SECONDS + AJ_DOWNLOAD_SECONDS))
export AJ_ACCEPTANCE_ROOT=/actual/retained/mount/aj-native-model-acceptance
export H3_MODEL_ACCEPTANCE_ROOT="$AJ_ACCEPTANCE_ROOT"
python3 h3/scripts/model_acceptance.py --root "$AJ_ACCEPTANCE_ROOT" --plan
# Przygotowuje wyłącznie marker/prywatny store, po sprawdzeniu disk.
python3 h3/scripts/model_acceptance.py --root "$AJ_ACCEPTANCE_ROOT" --prepare
mkdir -m 700 -p "$AJ_ACCEPTANCE_ROOT/evidence"
python3 h3/scripts/gpu_acceptance_operator.py \
  --session /private/operator-session.json --phase B --inspect
```

W oddzielnym prywatnym terminalu, przed downloadem/buildem:

```bash
python3 h3/scripts/gpu_acceptance_operator.py \
  --session /private/operator-session.json --phase B \
  --watch "$AJ_ACCEPTANCE_ROOT/evidence/B-telemetry-unique.jsonl"
```

Po osobnej zgodzie operatora, w pierwszym terminalu:

```bash
set +x
# HF_TOKEN dostarcz przez zaufany environment, nigdy w komendzie/history/logu.
if timeout --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_INSTALL_SECONDS}s" \
  bash h3/INSTALL_MODEL_ACCEPTANCE.sh --install \
  > "$AJ_ACCEPTANCE_ROOT/evidence/B-install-private.log" 2>&1; then
  AJ_INSTALL_RC=0
else
  AJ_INSTALL_RC=$?
fi
unset HF_TOKEN HF_HUB_TOKEN HUGGING_FACE_HUB_TOKEN
printf 'Install exit code: %s\n' "$AJ_INSTALL_RC"
test "$AJ_INSTALL_RC" = 0
"$AJ_ACCEPTANCE_ROOT/.h3-venv/bin/python" h3/scripts/model_acceptance.py \
  --root "$AJ_ACCEPTANCE_ROOT" --verify \
  > "$AJ_ACCEPTANCE_ROOT/evidence/B-models-runtime-verified.json"
```

Sprawdź return code obu poleceń: nonzero to STOP. Pełna weryfikacja 11 SHA
jest wymagana, także po lokalnym/CPU transferze. Przy błędzie hash/rozmiaru
nie usuwaj w ciemno ani nie podmieniaj artefaktu. Native verification wykonuje
wyłącznie `--version`, bez load/generation. Submissions pozostają OFF.

Monitor wymaga odświeżania **tylko sekcji billing** prywatnego session JSON
przed zatwierdzonym `billing_max_age_seconds`. Odświeżenie wykonaj atomowo z
uprawnieniami 0600. Zmiana quote, approvals, SHA, mountu lub limitów podczas
observation oznacza STOP; nowe limity wymagają nowego review. Monitor pokazuje
elapsed time, actual observed bill, estymatę z reserves, sampled peak VRAM.
STOP/INTERRUPTED nie kończy native procesu ani provider billingu: operator
musi zareagować w drugim terminalu. Nie zostawiaj tej sesji bez nadzoru.

`session_started_utc` jest niezmienny. `observed_at_utc` i `observed_total_usd`
mogą tylko rosnąć; korekta faktury/credit, reset lub inny punkt startu wymagają
STOP i osobnego review, zamiast cichego zwiększenia dostępnego budżetu.
Monitor rezerwuje też `external_preparation_usd`, niezależnie od bieżącej
faktury instancji. Transfer/rsync staging uwzględnij w zatwierdzonej rezerwie
`build_reserve_bytes`; nie jest automatycznie wolnym miejscem.

Hard timeout `--install` i monitor B ograniczają **całą instalację**, nie
rozpoznają automatycznie granicy build/download. Operator zapisuje osobne
start/end buildu i transferu z install log oraz stagingu; pilnuje również
`max_build_seconds` i **`max_download_seconds` osobno**, przerywając własny
installer, gdy którykolwiek upłynie. Jeżeli tej granicy nie można obserwować,
STOP; użyj wcześniej osobno zatwierdzonego CPU `--download` z własnym
`timeout ... "${AJ_DOWNLOAD_SECONDS}s"`, a GPU installer ma już tylko rehash/reuse.
Nie traktuj niewykorzystanego czasu build jako zgody na dłuższy download.

## 7. C — pojedyncze rzeczywiste próby modeli

### Przygotowanie prywatnego przypadku i dowodów

Przed C ustaw `model_probes=true`, przejdź ponownie hardware/mount/idle/billing
checks i pełne file/native verification. Nie uruchamiaj dwóch prób równolegle.

```bash
set -euo pipefail
umask 077
AJ_PREFLIGHT_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["max_preflight_seconds"])')
test "$AJ_PREFLIGHT_SECONDS" != None
timeout --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_PREFLIGHT_SECONDS}s" \
  "$AJ_ACCEPTANCE_ROOT/.h3-venv/bin/python" h3/scripts/gpu_acceptance_operator.py \
  --session /private/operator-session.json --phase C --inspect \
  > "$AJ_ACCEPTANCE_ROOT/evidence/C-preflight-private.json"
export AJ_NATIVE="$AJ_ACCEPTANCE_ROOT/native/custom_nodes/ComfyUI-LLM-text-processor/vendor/llama.cpp/b10472/linux-x64-cuda"
export AJ_MODELS="$AJ_ACCEPTANCE_ROOT/models/LLM"
export AJ_CASE="$AJ_ACCEPTANCE_ROOT/evidence/C-unique-case"
mkdir -m 700 "$AJ_CASE"
"$AJ_NATIVE/llama-cli" --version > "$AJ_CASE/cli-version.txt" 2>&1
"$AJ_NATIVE/llama-server" --version > "$AJ_CASE/server-version.txt" 2>&1
```

Każda próba ma własny katalog, którego nie nadpisujemy. W prywatnym
`attempt.json` zapisz: checkout/model ID/revision/SHA, dokładny argv (bez
sekretów), seed, requested/effective duration, GPU/driver/CUDA, runtime receipts,
czas UTC start/load/request/end, return code, token usage/throughput,
sample cadence i sampled peak VRAM, KV/cache/workspace bytes z native logów,
native response hash i wynik validation. Statusy startują **NOT RUN**.
Plików z prywatną treścią nie dołączaj do publicznego PR.

```text
evidence/
  session/quote-private.json, operator-session.json, hardware-mount.json
  B-models-runtime-verified.json, B-install-private.log, B-telemetry-unique.jsonl
  C-unique-case/
    attempt.json, command.json, input-identities.json, native-runtime.json
    private-inputs/, canonical-v20-source.json, instructions-source.json
    native.stdout, native.stderr, process-time.txt, gpu-telemetry.jsonl
    request-private.json, response-private.json, response.sha256
    step3-raw.txt, canonical-step3.txt, step4-validation.json
    review.json  # PASS/FAIL/NOT RUN i brakujące pomiary; nigdy secrets
```

Nie zapisuj całego `env`, shell history, kluczy SSH, HF/Vast/API tokens ani
autoryzowanych URL z query credentials. Komendy używają prywatnych plików
prompt/input; nie przekazuj treści promptu przez `-p` lub argv. stdout/stderr
native mogą zawierać prompt/reference/reasoning — przechowuj tylko prywatnie.

Limity shell pobierz z zatwierdzonego session, bez fallbacków:

```bash
export AJ_SESSION=/private/operator-session.json
AJ_PROBE_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["max_probe_seconds"])')
AJ_GRACE_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["shutdown_grace_seconds"])')
AJ_PREFLIGHT_SECONDS=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["max_preflight_seconds"])')
test "$AJ_PROBE_SECONDS" != None
test "$AJ_GRACE_SECONDS" != None
test "$AJ_PREFLIGHT_SECONDS" != None
```

Każdy A2/C readonly inspect (w tym pełne hashe) ogranicz własnym
`timeout --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_PREFLIGHT_SECONDS}s"`
przed komendą helpera. Przekroczenie limitu nie zmienia wyniku na PASS.
Pierwszy rehash monitora C wlicza się również do jego `max_probe_seconds`.
Timeout procesu i lokalny STOP nadal nie zatrzymują billingu providera.

Monitor C jest wyłącznie obserwatorem. Po pełnym rehashu przechowuje verified
file signatures i zgłasza STOP przy ich zmianie, mount loss, obcym GPU PID,
koszcie/czasie albo nieaktualnym billingu. Nie nadaje admission produkcyjnemu
Prompt Workerowi i nie zabija PIDów. `--allow-gpu-pid` to jawna, zweryfikowana
przez operatora lista PIDów **obserwowanych przez nvidia-smi** dla jednej własnej
próby; nie dowód własności i nie zgoda na signals. Jeżeli host/container PID
namespaces nie dają się jednoznacznie powiązać, STOP zamiast zgadywania PID.
Zacznij telemetry przed odblokowaniem startu modelu, używając własnego
foreground wrappera/czekającego procesu, którego PID i mapping sprawdzono.
Bez tej kontroli użyj surowych GPU samples wyłącznie diagnostycznie i oznacz
brak load peak; nie oznaczaj kompletnego memory acceptance jako PASS.

```bash
# AJ_OWNED_GPU_PID operator wiąże z własnym konkretnym native procesem.
python3 h3/scripts/gpu_acceptance_operator.py --session "$AJ_SESSION" --phase C \
  --allow-gpu-pid "$AJ_OWNED_GPU_PID" --watch "$AJ_CASE/gpu-telemetry.jsonl"
```

Sampled peak jest dolnym ograniczeniem rzeczywistego peak, nie pomiarem każdego
CUDA allocation. Zachowaj cadence, native KV/buffer logs i initial/final memory.
Brak KV/load time/token stats oznacz jako NOT RUN/niezmierzone; nie licz ich z
rozmiaru GGUF ani z elapsed time obejmującego hash/build.

### C2 priorytet — Qwen3.5-27B, następnie C3 Qwen3-14B

Poniższe **proponowane** minimalne probe parameters (128/4096, reasoning OFF,
50 GPU layers z canonical native ustawienia) wymagają review komendy przez
operatora. Nie są domyślnym budżetem finansowym ani Step 4 acceptance.
`minimal-text.txt` zawiera rzeczywiste zadanie operatora; nie używaj fixtures z
`tests/`. Sprawdź real offload/layers/tokenizer/template i powstałe logi.
`AJ_SEED` wpisz z zatwierdzonego prywatnego case; nie ma automatycznego seed.

```bash
timeout --foreground --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_PROBE_SECONDS}s" \
  "$AJ_NATIVE/llama-cli" -m "$AJ_MODELS/Qwen3.5-27B-heretic.Q5_K_M.gguf" \
  -f "$AJ_CASE/private-inputs/minimal-text.txt" -n 128 -c 4096 -ngl 50 --seed "$AJ_SEED" \
  --single-turn --reasoning off --reasoning-format deepseek --reasoning-budget 0 \
  > "$AJ_CASE/native.stdout" 2> "$AJ_CASE/native.stderr"
AJ_PROBE_RC=$?
printf 'Probe exit code: %s\n' "$AJ_PROBE_RC"
```

Utwórz nowy case i powtórz **osobno**, zmieniając tylko model na
`Qwen3-14B-heretic.Q4_K_M.gguf`. RC 124/137/143, OOM, brak/niedokończony output
albo template/kernel error to FAIL. Sam exit 0 i tekst z `llama-cli` nie dowodzą
naturalnego EOS: CLI nie zachowuje pełnego finish status. Te próby sprawdzają
wyłącznie wstępny load/minimal generation; nie odblokowują DavidAU.

### C4 — oba Mistrale i BF16 projector z rzeczywistymi obrazami

Przygotuj prywatny rzeczywisty obraz i vision prompt, zapisz jego hash/dimensions.
Minimalny pojedynczy obraz to test zgodności projektu vision, nie dowód pełnego
v20 six-reference pipeline. C5 wymaga później faktycznej analizy **sześciu** refs.

```bash
timeout --foreground --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_PROBE_SECONDS}s" \
  "$AJ_NATIVE/llama-cli" \
  -m "$AJ_MODELS/Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-Q4_K_M.gguf" \
  --mmproj "$AJ_MODELS/Mistral-Small-3.2-24B-Instruct-2506-mmproj-BF16.gguf" \
  --image "$AJ_CASE/private-inputs/real-reference.png" -f "$AJ_CASE/private-inputs/vision-prompt.txt" \
  -n 128 -c 4096 -ngl 50 --seed "$AJ_SEED" --single-turn --reasoning off \
  --reasoning-format deepseek --reasoning-budget 0 \
  > "$AJ_CASE/native.stdout" 2> "$AJ_CASE/native.stderr"
```

Nowy case dla Q6_K, ten sam BF16 projector i rzeczywisty obraz. Sprawdź brak
embedding/tensor mismatch, actual image encoding i zachowanie po unload.
Nie podstawiaj projektora Heretic-v1.2-2 ani F16 z wcześniejszego fine-tune.

### C5 — prywatne rzeczywiste wejścia v20 i Step 3

Przed controlled Step 3/4 przygotuj zatwierdzony prywatny case: 6 refs, at most
1 audio, rzeczywisty user intent i **rzeczywiste** outputs Step 0–2. Minimalne
vision/test strings powyżej nie mogą zastąpić tej analizy. Brak tych danych
blokuje C5, choć wstępne model probes mogą już mieć własne receipts.

Zachowaj byte-exact source i eksport wszystkich canonical instruction strings:

```bash
cp h3/workflows/H3_v20_heretic_MASTER_DURATION_EXACT.json "$AJ_CASE/canonical-v20-source.json"
python3 - "$AJ_CASE/instructions-source.json" <<'PY'
import hashlib,json,sys
from pathlib import Path
from h3.app.workflow_graph import instruction_snapshot
p=Path('h3/workflows/H3_v20_heretic_MASTER_DURATION_EXACT.json')
snapshot=instruction_snapshot(json.loads(p.read_text()))
result={'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
        'instructions':[{'graph':g,'node':n,'field':f,'text':text} for (g,n,f),text in snapshot.items()]}
with open(sys.argv[1],'x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
PY
```

To jest source snapshot, **nie gotowy effective prompt**. Operator przygotowuje
`step3.prompt.txt` / późniejszy `step4.prompt.txt` zgodnie z upstream links,
StringFormat/concatenations canonical v20, actual outputs i duration locks,
bez parafrazowania instrukcji. Zachowuje lineage każdego runtime inputu.
Jeżeli nie można dowieść zgodności effective inputs z tym grafem, C5 jest
BLOCKED; nie generuj zastępczego promptu ani fixture'u. Pełna analiza/audio
jest osobną częścią rzeczywistych dowodów, nie wnioskiem z CPU tests.

Step 3, dla każdego właściwego writera, reasoning **OFF**, budget **0**,
max **8192**, context **24576**. Do controlled receipt probe można użyć
przypiętego sibling servera z jednym requestem. Produkcyjne Step 0–3 wymagają
teraz tego samego native receipt przez prywatny one-shot adapter, z zachowaniem
obrazów/projektorów, parametrów i GPU leases; [poprawka F2](BUGFIX_F1_F4_2026-10-14.md).
To ten sam pinned engine, bez migracji runtime ani współdzielonego servera.

Operator wybiera nieużywany prywatny port `AJ_PORT` poza 7860/8188/8189 i zatwierdza
seed. W osobnym terminalu uruchamia **jeden foreground** server pod własnym timeout:

```bash
timeout --foreground --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_PROBE_SECONDS}s" \
  "$AJ_NATIVE/llama-server" -m "$AJ_MODELS/Qwen3.5-27B-heretic.Q5_K_M.gguf" \
  -c 24576 -n 8192 -ngl 50 --reasoning off --reasoning-format deepseek --reasoning-budget 0 \
  --host 127.0.0.1 --port "$AJ_PORT" --parallel 1 --no-context-shift \
  > "$AJ_CASE/native.stdout" 2> "$AJ_CASE/native.stderr"
```

Request jest nonstreaming, z **verbose=true**, actual private promptem i
max_tokens 8192. Po native successful EOS/finish/no truncation należy wyciągnąć
content i zastosować **istniejący** `normalize_plan` do legal effective duration.
Nie akceptuj answer zawierającego reasoning, pustego planu ani receipt bez EOS.
Existing Stage 4 parser wymaga sześciu sekcji, więc nie używaj go jako łagodnego
parsera surowego Stage 3. Native receipt Stage 3 ma wymagania identyczne jak
wejściowe checks `completion_response` (stop=true/type=eos, truncated=false,
one choice/index0/finish=stop/assistant/no tools), a final timeline przechodzi
existing `normalize_plan` + `canonical_plan`. Zapisz raw/native evidence do review.

Po **ręcznym review pełnego native receipt** i zapisaniu rzeczywistego final
channel w prywatnym `step3-raw.txt`, normalizuj tym samym temporal contract:

```bash
python3 - "$AJ_CASE/step3-raw.txt" "$AJ_CASE/canonical-step3.txt" "$AJ_REQUESTED_DURATION" <<'PY'
import sys
from h3.app.temporal import duration_identity,normalize_plan,canonical_plan
from h3.scripts.check_model_completion import bounded_text
identity=duration_identity(float(sys.argv[3]))
raw=bounded_text(sys.argv[1])
if not raw.strip() or any(tag in raw for tag in ('<think>','</think>','[Start thinking]','[End thinking]')):
    raise SystemExit('Empty/ambiguous Step 3 final channel')
plan=normalize_plan(raw,identity['effective_duration_seconds'])
canonical_plan(plan,identity['effective_duration_seconds'])
with open(sys.argv[2],'x') as stream:stream.write(plan)
PY
```

Ten snippet **nie parsuje ani nie poświadcza EOS**. Bez powyższego native
receipt review status Step 3 pozostaje NOT RUN; poprawny temporal output sam
nie wystarcza do PASS. W Step 4 użyj offline checkera poniżej bez zmian.

### C6 — Step 4 dla 10Eros, następnie H3

| Profil | Model | Reasoning/budget | max_tokens | ctx_size | Final/framing |
| --- | --- | --- | ---: | ---: | --- |
| 10Eros Full | Qwen3.5-27B Heretic Q5_K_M | ON / **8192** | **16448** | **36864** | **8192 / 64** |
| H3 Full | Qwen3-14B Heretic Q4_K_M | ON / **6144** | **14400** | **32768** | **8192 / 64** |

Step 3/4 actual output dla request 20 s używa **481 frames**, canonical timeline
**20.040 s** (frame duration 481/24 ≈20.041667 s). Nie zastępuj tego końcem
20.000 lub raw 90 s. Prompt Step 4 musi zawierać już canonical Step 3.

Dla 10Eros uruchom osobny, pojedynczy server (zamknij wcześniejszy) w foreground:

```bash
timeout --foreground --signal=TERM --kill-after="${AJ_GRACE_SECONDS}s" "${AJ_PROBE_SECONDS}s" \
  "$AJ_NATIVE/llama-server" -m "$AJ_MODELS/Qwen3.5-27B-heretic.Q5_K_M.gguf" \
  -c 36864 -n 16448 -ngl 50 --reasoning on --reasoning-format deepseek --reasoning-budget 8192 \
  --host 127.0.0.1 --port "$AJ_PORT" --parallel 1 --no-context-shift \
  > "$AJ_CASE/native.stdout" 2> "$AJ_CASE/native.stderr"
```

W drugim terminalu przygotuj private request z actual effective promptem.
Przykład dla Step 4 10Eros; seed i duration są świadomie zaakceptowane w case:

```bash
python3 - "$AJ_CASE/private-inputs/step4.prompt.txt" "$AJ_CASE/request-private.json" "$AJ_SEED" <<'PY'
import json,sys
from pathlib import Path
prompt=Path(sys.argv[1]).read_text()
if not prompt.strip():raise SystemExit('Missing actual v20 input')
body={'messages':[{'role':'user','content':prompt}], 'stream':False, 'verbose':True,
      'max_tokens':16448,'temperature':0.8,'top_p':0.95,'top_k':64,'repeat_penalty':1.05,'seed':int(sys.argv[3])}
with open(sys.argv[2],'x') as stream:json.dump(body,stream)
PY
# Najpierw readiness /health; nie publikuj portu, nie używaj proxy dla loopback.
curl --noproxy '*' --fail --silent --show-error --max-time "$AJ_PROBE_SECONDS" \
  "http://127.0.0.1:$AJ_PORT/health" > "$AJ_CASE/health-private.json"
curl --noproxy '*' --fail --silent --show-error --max-time "$AJ_PROBE_SECONDS" \
  --max-filesize 16777216 -H 'Content-Type: application/json' \
  --data-binary "@$AJ_CASE/request-private.json" \
  "http://127.0.0.1:$AJ_PORT/v1/chat/completions" > "$AJ_CASE/response-private.json"
sha256sum "$AJ_CASE/response-private.json" > "$AJ_CASE/response.sha256"
"$AJ_ACCEPTANCE_ROOT/.h3-venv/bin/python" h3/scripts/check_model_completion.py \
  --completion "$AJ_CASE/response-private.json" --timeline "$AJ_CASE/canonical-step3.txt" \
  --duration "$AJ_REQUESTED_DURATION" > "$AJ_CASE/step4-validation.json"
```

Sprawdzaj exit status **każdego** polecenia; powyższe bloki nie wykonują fallbacku.
Po request zamknij własny server, zanim przejdziesz dalej. H3: nowy case,
Qwen3-14B, context32768/max14400/budget6144, payload `max_tokens=14400`.
Step 3: nowy case i jego właściwy writer, OFF/0/context24576/max8192,
payload temperature0.95 i rzeczywisty Step 3 prompt. Nie obniżaj budgets dla PASS.

Step 4 waliduje ten sam `completion_response` i temporal contract co produkcja:
natural EOS, successful finish, no truncation, osobny final/reasoning,
**sześć kompletnych sekcji i pełny zgodny timeline**. Błąd/length/context/empty
final/unclosed thinking oznacza FAIL. `gpu_acceptance=not_attested` w checkerze
pozostaje obowiązujący; operator review actual source/command/hardware jest
konieczny. Nie projektujemy nowego łagodniejszego completion parsera.

### C7 — metryki i review każdej próby

Do każdej własnej komendy native/curl dołącz przed jej startem
`date -u +'%Y-%m-%dT%H:%M:%SZ'` do prywatnego `start.utc`, a po zakończeniu
do `end.utc`. Jeżeli obraz ma GNU time, uruchom własny timeout pod
`/usr/bin/time -v -o "$AJ_CASE/process-time.txt"`; zachowaj jego exit code.
`Maximum resident set size` jest CPU RSS, **nie VRAM**. Readiness timestamp
zapisz po udanym `/health`; samo load time z native logs oddziel od czasu
requestu i hashów. Użyj nowego katalogu dla kolejnej próby, bez overwrite.

Po zapisaniu pełnego rzeczywistego receipt można wyeksportować native metryki:

```bash
python3 - "$AJ_CASE/response-private.json" "$AJ_CASE/native-metrics-private.json" <<'PY'
import json,sys
from h3.scripts.check_model_completion import bounded_text
body=json.loads(bounded_text(sys.argv[1]))
detail=body.get('__verbose',{})
metrics={k:body[k] for k in ('usage','timings') if k in body}
metrics['native_detail']={k:detail[k] for k in ('timings','tokens_evaluated','tokens_predicted',
    'stop','stop_type','truncated') if k in detail}
metrics['gpu_acceptance']='NOT RUN; metrics extraction is not completion validation'
with open(sys.argv[2],'x') as stream:json.dump(metrics,stream,indent=2)
PY
```

Zachowaj `__verbose`, usage i timings **w całości prywatnie**, nie tylko final
text. Zapisz prompt/generated/reasoning/final token counts według native fields,
prompt/eval rates i czasy; nie twórz rozdzielonych counts, jeżeli engine ich nie
raportuje. Model load time określ z timestampów procesu i native load/ready logs,
generation z native timings i request start/end. Dla cold/warm rozróżnij page/KV
cache i sposób offload. Szczegółowe GPU/buffer/KV logs są źródłem pomiarów;
GGUF bytes nie są oszacowaniem peak VRAM. Nie wykonuj overlap benchmarku ani
zmian policy VRAM w Phase 1.

PASS dotyczy tylko konkretnego wykonanego kryterium; lista braków pozostaje
NOT RUN. Wstępny model load nie poświadcza whole pipeline/render/GPU acceptance.
Priorytet review: rzeczywisty Qwen3.5-27B native load/generation i pełny Step 4
reasoning/final/EOS/context/timeline, następnie H3 writer i oba Mistrale/projector.

## 8. STOP, odłączenie i zakończenie bez utraty danych

1. Przy OOM, checksum/source/driver/GPU error, incomplete EOS/final, lost mount,
   unknown process lub budget/deadline: **nie zaczynaj kolejnego kroku**.
2. Przerwij własną foreground próbę Ctrl-C/TERM. GNU timeout dotyczy wyłącznie
   procesu, który sam uruchomił; kill-after używa zatwierdzonego grace. Nie
   SIGSTOP'uj CUDA i nie wysyłaj sygnałów na PID z nvidia-smi bez dowodu mapping/ownership.
   Jeżeli własny proces nie kończy się, zachowaj log i wyjaśnij stan przed dalszą pracą.
3. Sprawdź faktyczne zakończenie własnych native/install dzieci i brak pracy GPU.
   Stop samego monitora nie zatrzymuje próby. Nie zatrzymuj cudzych usług.
   **Phase 1 nie zapewnia produkcyjnego, atomowego sterowania grupą przez
   pidfd.** TERM jest prośbą o zakończenie; KILL nie wykonuje cleanup użytkownika
   i nie gwarantuje natychmiastowego wyjścia procesu z nieprzerywalnego I/O.
   GNU `timeout --foreground` nadzoruje bezpośrednią komendę; jej dzieci nie są
   automatycznie objęte timeoutem. Timeout bez `--foreground` może sygnalizować
   swoją grupę, ale nie obejmuje dzieci, które ją opuściły, i nie daje gwarancji
   ownership/restart fencing produkcji. RC timeoutu lub koniec rodzica nie są
   dowodem zakończenia całego buildu, downloadera ani pracy GPU.
   Operator pozostaje przy sesji, sprawdza własne procesy/dzieci i mapping PID
   host/container oraz potwierdza zwolnienie GPU przed następnym krokiem.
   Nie sygnalizuje ponownie zapamiętanego numeric PID/PGID bez aktualnego dowodu
   własności. Przy niepewności STOP i ręczne wyjaśnienie stanu; bez restartu,
   kolejnej próby ani automatycznego lifecycle. Lokalny TERM/KILL nie kończy
   billingu; osobny zatwierdzony provider STOP pozostaje obowiązkiem operatora.
4. Potwierdź actual mount/attachment, chmod/private logs, zakończenie write/fsync,
   SHA/index receiptów i obecność danych na retained volume. Przy utracie mountu
   **nie twórz na tym samym pathname nowego katalogu ani nie wznawiaj downloadu**.
   Przywróć właściwy attachment i ponownie zweryfikuj jego tożsamość.
5. Zachowaj staging i poprawne, fully verified weights. Po restarcie re-run
   readonly plan/inspection i existing `--verify`; źródło/approval/root drift
   wymaga nowego reviewed store. Nie kasuj danych tylko po to, by zwolnić miejsce.
6. Zabezpiecz prywatny indeks wyników i potrzebny backup poza jedynym hostem.
   Backup/copy także wymaga zatwierdzonego czasu/transfer cost. Po restore pełne
   hashes i nowa independent mount verification; lokalne inode receipts nie
   stanowią przenośnego proof.
7. Zakończ Phase 1 review. **Nie startuj ComfyUI/panelu/renderów/lifecycle tests**.
   Jeżeli manual provider STOP był zatwierdzony przed kosztem, operator osobno
   zatrzymuje swoją wynajętą instancję w console po punktach powyżej i sprawdza
   rzeczywisty stan **Stopped** oraz final billing. Nie używamy AJ lifecycle API.
   To kontrola kosztu, nie dowód poprawności produkcyjnych STOP/DESTROY.
8. Volume i stopped instance mogą nadal naliczać storage. Ustal datę następnego
   review i odpowiedzialną osobę. Nie zostawiaj nieograniczonej retencji bez
   budżetu. Stopped GPU może nie być dostępny przy wznowieniu.
9. **Nie wykonuj DESTROY ani delete volume w Phase 1**. Bez independently verified
   retention/backup nie wolno uznawać, że dane przeżyją. Destrukcyjne zamknięcie
   to osobno zatwierdzona decyzja; żaden nowy skrypt jej nie wykonuje.

[Managing Instances](https://docs.vast.ai/guides/instances/manage-instances)
rozróżnia Stop, continued storage charges i destrukcyjne usunięcie instancji.
Dokumentacja Local Volume opisuje osobną retencję i permanent volume deletion.
Nie utożsamiaj container disk z retained Local Volume ani nie traktuj STOP jako
gwarancji późniejszej dostępności tego samego GPU. Program tylko raportuje
`billing_continues=true` przy STOP/INTERRUPTED.

## 9. Co operator musi zatwierdzić przed pierwszym kosztem

1. Reviewowany commit PR #8, source locks 11 GGUF, pinned runtime i **zakres A–D**
   bez production/render/lifecycle; osobne approvals B i pojedynczych prób C.
2. Konkretną ofertę/machine, dokładny GPU/edition/VRAM, driver/CUDA devel/image
   digest, kernel, RAM/vCPU, kontrakt/end date, private access i brak auto-onstart.
3. Wybrany wariant preparation/transfer, rzeczywisty Local Volume na docelowym
   hostcie, pojemność, mount/attachment plan, build/evidence/transfer rezerwy i backup.
4. **Max USD/h, max total USD, max session time, min free bytes, max download time**;
   ponadto max preflight/review/build/probe time, polling, shutdown grace,
   billing freshness i retencję. Limit USD/h obejmuje sumę active compute/disk/volume.
   Wszystkie konkretne stawki GPU/storage/ingress/egress/external prep, ich jednostki
   i sumę wraz z retry/backup/retention; żadnego null ani arbitralnego fallbacku.
5. Monitoring actual invoice/salda/auto-top-up oraz odpowiedzialność za ręczny STOP
   compute i continued storage cost. Wydatek nie może zależeć od automatycznego DESTROY.
6. Warunkowy start: A2 musi potwierdzić actual GPU/mount. Przy niepowodzeniu nie ma
   zgody na B/C; operator zabezpiecza dane i ręcznie ogranicza koszt zgodnie z planem.
7. Prywatny rzeczywisty case/refs/audio/input lineage, command parameters/seeds/duration,
   oczekiwane kryteria i prywatny katalog dowodów. Missing effective v20 inputs blokują C5.

Aktualnie te zatwierdzenia, live quote, actual GPU/mount, input case i wszystkie
próby pozostają **NOT RUN / BLOCKED**. Przygotowanie runbooka i CPU PASS nie
rozpoczyna płatnej sesji. Następny krok należy do osobnej decyzji operatora.
