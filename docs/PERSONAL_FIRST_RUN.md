# Personal First Run — H3 Full + 10Eros Full

Aktualna ścieżka dla jednej osoby: **wynajem → instalacja na dysku instancji →
dwa pojedyncze rendery → pobranie MP4 na telefon → ręczny DESTROY**.
Local Volume, osobny store GGUF i 23 rendery nie są wymagane. Po DESTROY wszystkie
dane mogą zniknąć. Pełny [Gate 1–6](GPU_PRODUCTION_SESSION.md) jest opcjonalną
diagnostyką. Nie wykonano jeszcze rzeczywistego GPU acceptance.

Nieużywany DavidAU 9B usunięto z aktywnego manifestu. Step 3 i Step 4 `10eros_full`
używają **Qwen3.5-27B-heretic.Q5_K_M.gguf** (`10eros-writer-replacement`),
revision `81ceaf141911d6ad2e0fbd008e5c043ebdd33fc5`, 19399608384 B,
SHA256 `66787dfbf5462ac355457efaf24e503d244f1083945243ba029080a8e6276322`.
Weryfikowane wagi można instalować przed inferencją. Nie ma procedury retirement
ani `allow_pending` w ścieżce instalacji. Status GPU pozostaje **pending**;
pierwszy rzeczywisty test poniżej poprzedza użycie autopromptera 10Eros. Jego
błąd oznacza STOP dla tego autopromptera, bez fallbacku do innego modelu.

## Przed wynajmem: oferta, pojemność i deadline

Nie ma jeszcze konkretnej oferty. **$15 / 4 h jest limitem planowania, nie
obietnicą wykonalności.** Przed pierwszym wydatkiem użytkownik wybiera i zatwierdza
prawdziwą ofertę: GPU, cały dysk, ingress/egress, dodatkowe opłaty i rozliczanie
zatrzymanego dysku. Zapis oferty i faktycznego billingu jest prywatny. Nie należy
wynajmować hosta po to, aby dopiero ustalać jego cenę lub niezbędne komendy.

| Zestaw | Dokładne bajty wag |
| --- | ---: |
| Wymagane 17 | 277909390584 |
| HM Breasts V2 + Bunny Bridge | 77580016 + 22045536 |
| Domyślne 19 | **278009016136** |
| Wszystkie 22, jawne `--include-optional` | 284661829208 |
| 11 GGUF, **podzbiór tego samego store** | 113395969792 |
| Pozostały transfer po przygotowaniu GGUF | 164613046344 |
| Największy plik, H3 Full BF16 | 66280487368 |

Domyślne LoRA bez zmian: H3 HM ON 1.0, MysticXXX OFF 0.6, Movement OFF 0.5;
10Eros opcjonalne OFF; Bunny według rejestru. Wyłączone LoRA nie wymagają wag.
Dodanie/włączenie LoRA wymaga zweryfikowania używanego zasobu, a nie reinstalacji
ani powtórzenia całego acceptance. Patrz [LoRA](LORA_CONFIGURATION.md).

Model-only final i peak zimnej instalacji to **278009016136 B**, nie suma dwóch
store i dodatkowego stagingu. Pobierany prefix zajmuje miejsce przyszłego modelu;
rename nie kopiuje danych. Nie zakładamy hardlinków, reflinków ani deduplikacji.
Wcześniejsze obce partials i cache zajmują realne miejsce i nie są kasowane
na podstawie domysłu. Plan `--plan` liczy pozostałe bajty, nie dowodzi poprawnego
SHA istniejących plików; instalator zawsze ponownie weryfikuje ich integralność.

Cały peak = użycie bazowego obrazu + 278009016136 + produkcyjny Python/Torch/Comfy
+ małe środowiska acceptance/downloader + source/build llama.cpp + metadane/cache
+ tymczasowe pakiety kompilacji + wejścia/wyniki/dowody + rezerwa użytkownika.
Nie zmierzono tych zależności na docelowym obrazie GPU. Jako **założenia do
sprawdzenia**, przewidź 15–25 GB na Python/Comfy/deps, 5–15 GB na build/source,
1–3 GB na małe środowiska/metadane i 5–10 GB na wejścia/wyniki/rezerwę.
To dodatkowe 26–53 GB, czyli **304.01–331.01 GB plus bazowy obraz i ewentualny
chwilowy rozpakowany build/pakiet**. Final może być mniejszy od peak po zamknięciu
instalacji, ale nie odejmuj cache/buildów zanim ich rzeczywiście nie usunięto.

Cel **350 decimal GB** daje 71990983864 B ponad modelami; 320 GB daje tylko
41990983864 B. 350 GB jest rozsądnym kandydatem, **warunkowym** na `df`, użycie
obrazu i powyższe rezerwy; 320 GB wymaga niższego, potwierdzonego zużycia.
Nie ma offline dowodu dokładnego całego peak ani gwarancji, że oferta „350 GB”
udostępnia tyle wolnych bajtów. Jeśli plan nie mieści się w rzeczywistym free,
STOP: wybierz lżejszy obraz lub większy dysk z uzasadnionym rachunkiem. Brak
miejsca nie usprawiedliwia INT8/Turbo lub pominięcia pełnego SHA256.

Downloader używa przypiętych SDK `huggingface-hub==2.2.0`, `hf-xet==1.7.0`.
High-level downloader 2.2 usuwa nieudany temporary file, dlatego używamy jego
transportów zakresów: HTTP `resume_size` albo **ordered Xet `download_stream`
start/end**, także dla pliku >50 GB. Zapisujemy jeden związany ze źródłem prefix
w `models/.downloads/<id>/.payload.incomplete`. Xet chunk cache jest wyłączony,
ponieważ resume zapewnia ten prefix i żądanie brakującego zakresu; nie tworzymy
kopii w Hub snapshot cache ani pliku reconstruction. HF metadata/logi pozostają
pod `HF_HOME` na dysku instancji. Pip cache jest domyślnie wyłączony, zatem retry
pakietów może ponownie pobrać pakiety. Przerwanych prefixów wag nie kasujemy.
Resume nie gwarantuje zerowego powtórnego transferu na poziomie sieci/chunków.

Kosztorys, GB = 10^9 B:

```text
C = h*(GPU_USD/h + instance_disk_USD/h)
  + ingress_GB*ingress_USD/GB + egress_GB*egress_USD/GB
  + preparation + inne_opłaty + rezerwa_cleanup
C <= 15; h <= 4, razem z instalacją, SHA, próbami i usunięciem instancji.
```

Ingress wag = **278.009016136 GB raz**; nie dodawaj ponownie 113.4 GB GGUF.
Dolicz obraz/pakiety/source, retries i wysyłane wyniki. Nie zakładaj transferu
ani kopiowania za darmo. Same cztery godziny zostawiają najwyżej $3.75/h na
compute+dysk tylko przy zerowych innych opłatach. Przy przepustowości M Mbit/s
sam transfer trwa co najmniej `8*278009016136/(M*1e6)` sekund: przy 1 Gbit/s
37.1 min, 500 Mbit/s 74.1 min, 200 Mbit/s 185.3 min. To dolne granice, bez
instalacji, SHA i renderów. Wielokrotne pełne SHA są mierzone, nie ukryte w ETA.
Przy 4 h granica ~154.45 Mbit/s zostawia **zero czasu** na pozostałe etapy.
Bez prawdziwej ceny i przepustowości nie ma decyzji GO na $15/4 h.

## Pierwsze 15 minut po SSH — bez downloadu

1. W prywatnym terminalu sprawdź instancję/maszynę/ofertę i start naliczania w
   panelu Vast. Ustaw alarm końca sesji oraz wcześniejszy alarm cleanup. Nie
   przekazuj API key do repo ani logów. Sprawdź, że onstart nie uruchomił obcych
   usług ani inferencji. Obce/nieznane procesy GPU oznaczają STOP.
2. Zweryfikuj checkout istniejącego Draft PR #8, jego zatwierdzony SHA i czystość:

```bash
set -euo pipefail
set +x
umask 077
cd /workspace/ziomalskit
: "${AJ_EXPECTED_SHA:?Wpisz dokładny zatwierdzony finalny SHA z PR przed sesją}"
test "$(git rev-parse HEAD)" = "$AJ_EXPECTED_SHA"
test -z "$(git status --porcelain --untracked-files=normal)"
python3 - <<'PY'
import hashlib, platform
from pathlib import Path
p=Path('h3/workflows/H3_v20_heretic_MASTER_DURATION_EXACT.json')
assert p.stat().st_size == 3084071
assert hashlib.sha256(p.read_bytes()).hexdigest() == '276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222'
assert tuple(map(int, platform.release().split('-')[0].split('.')[:2])) >= (6,9)
from h3.scripts.process_identity import require_group_control
require_group_control()  # Rzeczywisty PIDFD_SIGNAL_PROCESS_GROUP, nie tylko uname.
PY
nvidia-smi --query-gpu=uuid,name,driver_version,memory.total,compute_cap --format=csv
nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv
nvcc --version
ss -ltnp
findmnt --json --target /workspace --output TARGET,SOURCE,FSTYPE,OPTIONS,FSROOT
stat -c 'device=%d inode=%i' /workspace
stat -c 'root-device=%d' /
df -B1 /workspace
cat /proc/self/cgroup
cat /proc/self/mountinfo
cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current /sys/fs/cgroup/cpu.max
python3 -c 'import os; print(sorted(os.sched_getaffinity(0)))'
```

3. Wymagane: RTX PRO 6000 Blackwell klasy 96 GB, capability 12.0, CUDA Toolkit
   **13.0**, driver >=580.65.06 i zgodny Torch z instalatora. Sprawdź wszystkie
   widoczne nadrzędne cgroups, nie tylko root pliki: komenda pomiaru pełnej hierarchii poniżej. W namespace kontenera
   provider musi potwierdzić niewidoczne limity; nie utożsamiaj RAM hosta z RAM
   przydziału. Ustal progi RAM/CPU przed kosztownym buildem. Operator sprawdza
   affinity i widoczne root limity, **nie zastępuje audytu ancestorów**.
4. Potwierdź rzeczywistą pojemność instance disk w ofercie i `df`. Root/overlay
   jest dozwolony; ścieżka `/workspace` nie oznacza retencji. Nie twórz volume
   proof. Sprawdź SSH tunnel i dostęp z telefonu; nie publikuj 8188/8189/7860.

**GO** dopiero po zgodnych faktach, cenach i wolnym miejscu. Brak dowodu lub
czas przekraczający limit preflight → STOP i ręczny cleanup/destroy.

Pełny odczyt widocznych ancestorów cgroup v2 (bez zmian limitów):

```bash
python3 - <<'PY'
import json, os
from pathlib import Path
root = Path('/sys/fs/cgroup')
assert (root / 'cgroup.controllers').is_file(), 'STOP: this recipe requires cgroup v2; review v1 separately'
rows = []
# Map the current membership through the cgroup mount, including namespace roots.
membership = next(line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
mounts = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines() if ' - cgroup2 ' in line]
assert len(mounts) == 1 and mounts[0][4] == str(root), 'STOP: ambiguous cgroup mount'
fsroot = mounts[0][3]
if membership == '/': current = root
elif fsroot == '/': current = root / membership.lstrip('/')
else:
    assert membership == fsroot or membership.startswith(fsroot.rstrip('/') + '/')
    current = root / membership[len(fsroot):].lstrip('/')
assert current.is_dir() and current.resolve().is_relative_to(root.resolve()), 'STOP: unresolved current cgroup'
current = current.resolve()
while True:
    row = {'path': str(current)}
    for name in ('cpu.max', 'cpuset.cpus.effective', 'memory.max', 'memory.high', 'memory.current', 'memory.swap.max', 'pids.max'):
        path = current / name
        row[name] = path.read_text().strip() if path.exists() else None
    rows.append(row)
    if current == root: break
    current = current.parent
print(json.dumps({'affinity_cpus': sorted(os.sched_getaffinity(0)), 'visible_ancestry': rows}, indent=2))
PY
```

Na każdym poziomie sprawdź `memory.max - memory.current`, `memory.high`,
`cpu.max / period`, cpuset i affinity. Planowane wartości RAM/CPU w session JSON
muszą uwzględniać najmniejszy efektywny limit; nie przepisuj specyfikacji hosta.

## Konfiguracja jednej sesji

Wszystkie dalsze komendy wykonuje operator dopiero podczas osobno zatwierdzonej
płatnej sesji; przygotowanie tego dokumentu niczego nie pobiera ani nie uruchamia.

```bash
export WORKSPACE=/workspace H3_PERSISTENT_ROOT=/workspace H3_PERSISTENCE_MODE=ephemeral
export COMFY_ROOT=/workspace/ComfyUI PANEL_ROOT=/workspace/H3_VAST_MOBILE
export COMFY_MODELS_DIR="$COMFY_ROOT/models" H3_BUILD_ROOT=/workspace/.h3-build
export H3_MODEL_ACCEPTANCE_ROOT=/workspace/.aj-acceptance
export H3_MODEL_ACCEPTANCE_MODELS_ROOT="$COMFY_MODELS_DIR"
export HF_HOME=/workspace/.cache/huggingface PIP_NO_CACHE_DIR=1
export H3_INSTALL_TIMING_FILE=/workspace/install-timings.jsonl
export H3_CUDA_VERSION=13.0 H3_INSTALL_VAST_CLI=0
export H3_ALLOW_SUBMISSIONS=0 H3_ALLOW_DIAGNOSTIC_SUBMISSIONS=0 H3_ENABLE_TERMINAL=0
unset H3_PERSISTENT_VOLUME_PROOF VAST_API_KEY
```

Store musi być nieistniejący lub poprawnie oznakowany. Kolejność: ustaw zmienne,
`--prepare`, następnie `mkdir -m 700 .../evidence`. Przy istniejącym markerze z
innego source lock wybierz nowy katalog dowodów, zachowując tę samą models_root;
nie kopiuj 113 GB ani nie edytuj starego markera.

Przed wynajmem skopiuj `h3/config/gpu_personal_session.example.json` do prywatnego
pliku sesji (poza Git); na hoście uzupełnij rzeczywiste mount facts z `findmnt`
**TARGET**, jego device/inode, machine ID, źródło, filesystem i fsroot. `mount`
może być `/`; nie wpisuj `/workspace`, jeśli to tylko podkatalog mountu `/`.
`volume_id=null`, `mode=ephemeral`, volume rate/retention=0; brak volume jest
prawdziwym faktem, nie brakującą zgodą. `provider_attachment` oznacza tutaj
potwierdzenie instance disk, nie potwierdzenie Local Volume.
Wpisz wszystkie stawki, aktualny billing z provider UI, limity etapów i rezerwy.
Suma preflight/build/download/probe/review/render/cleanup musi zmieścić się w
14400 s; całkowity ceiling <=$15. Nie wpisuj zer dla nieznanych cen.
`download_bytes_cap` zawiera wagi, deps i retries, nie tylko same modele.

```bash
export AJ_SESSION=/workspace/private/operator-session.json  # rzeczywiście uzupełniony, mode 600
python3 h3/scripts/gpu_acceptance_operator.py --session "$AJ_SESSION" --phase A
python3 h3/scripts/gpu_acceptance_operator.py --session "$AJ_SESSION" --phase A --inspect
```

`BLOCKED` = brak GO. Operator nie wynajmuje, nie pobiera i nie wyłącza providera.
Sesję można znormalizować przez prywatny edytor JSON; nie publikuj jej w PR.
Budżet bez konkretnej oferty pozostaje nieuzupełniony.

## Przygotowanie 11 GGUF i pierwsza rzeczywista próba 27B

Preinstalowany obraz CUDA devel musi mieć Python >=3.11, venv, Git/CMake i nvcc.
Najpierw przygotuj **rzeczywisty przypięty checkout ComfyUI**, zanim powstanie
jego katalog models. Zapobiega to próbie instalacji do katalogu bez repozytorium:

```bash
AJ_COMFY_SHA=$(python3 h3/scripts/runtime_config.py constant COMFY_COMMIT)
if test ! -e "$COMFY_ROOT"; then
  git clone --no-checkout https://github.com/Comfy-Org/ComfyUI.git "$COMFY_ROOT"
  git -C "$COMFY_ROOT" fetch --depth 1 origin "$AJ_COMFY_SHA"
  git -C "$COMFY_ROOT" checkout --detach "$AJ_COMFY_SHA"
fi
test "$(git -C "$COMFY_ROOT" rev-parse HEAD)" = "$AJ_COMFY_SHA"
test -z "$(git -C "$COMFY_ROOT" status --porcelain --untracked-files=no)"
python3 h3/scripts/model_acceptance.py --root "$H3_MODEL_ACCEPTANCE_ROOT" --plan
python3 h3/scripts/model_acceptance.py --root "$H3_MODEL_ACCEPTANCE_ROOT" --prepare
mkdir -m 700 -p "$H3_MODEL_ACCEPTANCE_ROOT/evidence"
python3 h3/scripts/gpu_acceptance_operator.py --session "$AJ_SESSION" --phase B --inspect
```

Zatwierdź `prepare_models=true` dopiero po ocenie czasu/kosztu. Pobierz limity
bez fallbacków z session JSON. W drugim terminalu monitor B wymaga świeżego
rzeczywistego billingu; odświeżaj jedynie sekcję `billing`, nie quote/approvals:

```bash
python3 h3/scripts/gpu_acceptance_operator.py --session "$AJ_SESSION" --phase B \
  --watch "$H3_MODEL_ACCEPTANCE_ROOT/evidence/install-telemetry.jsonl"
```

Monitor tylko sygnalizuje STOP. Operator przerywa własny instalator i kontroluje
billing. Watch B/C dotyczy faz bez usług produkcyjnych; nie używaj go po ich
starcie, bo obecność panelu/workerów jest wtedy oczekiwana. Dalszą część sesji
nadzorują zewnętrzny billing ledger i alarmy deadline.

```bash
AJ_INSTALL_S=$(python3 -c 'import json,os; d=json.load(open(os.environ["AJ_SESSION"]))["limits"]; print(d["max_build_seconds"]+d["max_download_seconds"])')
AJ_GRACE_S=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["shutdown_grace_seconds"])')
# Token tylko jeżeli źródło wymaga autoryzacji, bez set -x i bez wartości w argv/history.
IFS= read -r -s -p 'HF token (puste dla publicznych źródeł): ' AJ_ONE_TIME_HF
printf '\n'
HF_TOKEN="$AJ_ONE_TIME_HF" timeout --foreground --signal=TERM --kill-after="${AJ_GRACE_S}s" "${AJ_INSTALL_S}s" \
  python3 h3/scripts/install_timing.py run native_prepare -- bash h3/INSTALL_MODEL_ACCEPTANCE.sh --install
unset AJ_ONE_TIME_HF HF_TOKEN
```

`--install` nie uruchamia inferencji ani usług. Tworzy mały osobny runtime i
oddzielne dowody; **wagi zapisuje bezpośrednio do `COMFY_ROOT/models`**. Build
llama.cpp na `H3_BUILD_ROOT` ma ten sam pin/receipts i zostanie ponownie użyty
przez pełny installer; binaria vendor są małymi zwykłymi plikami, nie wagami.
Retry tej samej komendy kontynuuje prefix, potem pełny SHA i no-replace rename.
Błędne SHA/source/symlink → STOP. Nie kasuj ogólnie `.downloads`; uszkodzony
payload usuwa się ręcznie dopiero po rozpoznaniu konkretnego nieudanego ID.
Po awarii po rename retry najpierw rehashuje final i odtwarza receipt offline.

Po file/native verification ustaw `model_probes=true`, wykonaj phase C inspect
(przy usługach nadal OFF), zamknij/rozlicz watcher B. Użyj własnego prywatnego
promptu tekstowego, nie fixtures testowych ani danych w argv:

```bash
python3 h3/scripts/gpu_acceptance_operator.py --session "$AJ_SESSION" --phase C --inspect
export AJ_CASE="$H3_MODEL_ACCEPTANCE_ROOT/evidence/qwen27b-first"
mkdir -m 700 "$AJ_CASE"
export AJ_NATIVE="$H3_MODEL_ACCEPTANCE_ROOT/native/custom_nodes/ComfyUI-LLM-text-processor/vendor/llama.cpp/b10472/linux-x64-cuda"
export AJ_MODELS="$COMFY_MODELS_DIR/LLM"
: "${AJ_SEED:?Ustal jawny seed przed próbą}"
: "${AJ_PRIVATE_PROMPT:?Ścieżka prywatnego, rzeczywistego promptu tekstowego}"
AJ_PROBE_S=$(python3 -c 'import json,os; print(json.load(open(os.environ["AJ_SESSION"]))["limits"]["max_probe_seconds"])')
set +e
timeout --foreground --signal=TERM --kill-after="${AJ_GRACE_S}s" "${AJ_PROBE_S}s" \
  "$AJ_NATIVE/llama-cli" -m "$AJ_MODELS/Qwen3.5-27B-heretic.Q5_K_M.gguf" \
  -f "$AJ_PRIVATE_PROMPT" -n 128 -c 4096 -ngl 50 --seed "$AJ_SEED" --single-turn \
  --reasoning off --reasoning-format deepseek --reasoning-budget 0 \
  > "$AJ_CASE/native.stdout" 2> "$AJ_CASE/native.stderr"
AJ_RC=$?
set -e
printf '%s\n' "$AJ_RC" > "$AJ_CASE/exit-code.txt"
test "$AJ_RC" -eq 0
nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv
```

W drugim terminalu próbkuj GPU/VRAM i obserwuj actual billing. Native logi są
prywatne. Sprawdź poprawny model/template, real offload, sensowną ukończoną
odpowiedź, brak OOM/kernel error i brak pozostałego własnego procesu. Zapisz SHA
logów, source/runtime/model identities, czas i wynik **tego jednego** testu.
Exit 0 nie dowodzi native EOS ani pełnego Step 3/4. Ten krótki test nie zmienia
manifestowego GPU pending w globalne validated. Nieudany test blokuje dalsze
uruchomienie autopromptera 10Eros. Nie maskuj błędu mniejszym writerem.
Opcjonalne pełne reasoning/EOS/Step0–4/vision próby opisuje Phase 1 / Gate 3.

TERM/KILL z `timeout --foreground` ogranicza własny proces pierwszej próby,
**nie gwarantuje zebrania dowolnych potomków**, nie naprawia CUDA ani nie kończy
billigu. Operator musi potwierdzić zakończenie własnego procesu i brak zajętego
GPU przed kontynuacją. Nie sygnalizuj obcych PIDów. W niejasnej sytuacji STOP;
po zabezpieczeniu wyników ręczny DESTROY. Produkcyjne usługi nadal używają
istniejącego menedżera z Linux >=6.9 i rzeczywistym pidfd group probe.

## Pełna instalacja i gotowość panelu

Po poprawnym minimalnym teście, bez retirement/cutover na wymyślony przyszły SHA,
pozostań na tym samym zatwierdzonym checkoutcie. Submissions nadal OFF:

```bash
IFS= read -r -s -p 'HF token (jeżeli wymagany): ' AJ_ONE_TIME_HF
printf '\n'
HF_TOKEN="$AJ_ONE_TIME_HF" python3 h3/scripts/install_timing.py run full_install -- bash h3/INSTALL_ON_VAST.sh
unset AJ_ONE_TIME_HF HF_TOKEN
source "$PANEL_ROOT/scripts/python_env.sh"
h3_select_python
bash "$PANEL_ROOT/scripts/preflight.sh"
bash "$PANEL_ROOT/scripts/smoke_test.sh"
python3 h3/scripts/install_timing.py summary "$H3_INSTALL_TIMING_FILE" > /workspace/install-summary.json
df -B1 /workspace
du -x -B1 --max-depth=1 /workspace
```

Pilnuj pozostałego globalnego czasu i stage limits: ponowna instalacja nie
restartuje budżetu $15/4 h. Pełny installer **zawsze** waliduje aktywny manifest,
source pin/size/SHA i istniejące 11 GGUF. Pobiera tylko pozostałe 164613046344 B
przy poprawnych plikach, plus deps. Nie kopiuje GGUF ani receiptów. Brakujące
opcjonalne OFF nie blokują instalacji. Zainstalowane są dwa ComfyUI workery
8188/8189 oraz prywatny panel7860; submission flag pozostaje0. Preflight ma mały
rzeczywisty test CUDA, smoke konwertuje/validuje bez render POST; są to przyszłe
komendy na wynajętym hoście, **nie zostały wykonane w przygotowaniu CPU**.

Timingi lokalne JSONL mode600: environment, zależności, Comfy install/custom
nodes, llama build, download każdego ID/bytes, SHA istniejących/staging/final,
full install, preflight, smoke. `summary` pokazuje elapsed monotonic od pierwszego
etapu, ostatni ukończony oraz failed/interrupted. Nie zapisuje argv, env, error
strings, haseł ani promptów. Nie mieszaj logów różnych bootów/sesji. GPU peak,
pełny final/peak disk, build duration i transfer throughput trzeba dopiero
zmierzyć; timestamp/CPU PASS nie są ich substytutem.

## Dokładnie dwa pierwsze rendery i telefon

Po preflight/smoke i sprawdzeniu prywatnego dostępu, przy **pustej kolejce**, włącz
submissions jawnie. Zostaw defaults renderu i LoRA. Nie twórz normalnego batcha
Autoprompt do testu pojedynczego renderu: taki batch ma **pięć auto-renderów**.

```bash
H3_ALLOW_SUBMISSIONS=1 "$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" write-env "$PANEL_ROOT/runtime.env"
bash "$PANEL_ROOT/scripts/service_ctl.sh" restart panel
```

Dostęp z telefonu: w jego kliencie SSH ustaw lokalny forward `7860` do
`127.0.0.1:7860` instancji, używając rzeczywistego hosta/portu/klucza z Vast.
Otwórz lokalny adres panelu w przeglądarce telefonu, zaloguj się Basic Auth.
Hasło jest w prywatnym `runtime.env` mode600; nie publikuj go ani nie zapisuj w
logach. Portów ComfyUI nie otwieraj publicznie. Przed płatnym renderem sprawdź
również /api/config i Diagnostics przez tę samą autoryzację.

Dla poniższego wariantu HTTP utwórz prywatny config curl w interaktywnym terminalu
SSH. Login i hasło czytane są z TTY, ponieważ stdin Pythona zajmuje heredoc;
hasło nie trafia do argv, historii shell ani logów. Podaj właściwe dane logowania
panelu, nie token HF/Vast:

```bash
mkdir -m 700 -p /workspace/private
export AJ_CURL_CONFIG=/workspace/private/curl.conf
python3 - <<'PY_AUTH'
import getpass, os, sys
from pathlib import Path
with open('/dev/tty', encoding='utf-8') as tty:
    print('Panel user: ', end='', file=sys.stderr, flush=True)
    user = tty.readline().rstrip('\n')
password = getpass.getpass('Panel password: ')
assert not any(c in user + password for c in '\r\n')
value = (user + ':' + password).replace('\\', '\\\\').replace('"', '\\"')
with Path(os.environ['AJ_CURL_CONFIG']).open('x') as f:
    os.chmod(f.name, 0o600)
    f.write('user = "' + value + '"\n')
PY_AUTH
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error http://127.0.0.1:7860/api/config
```

W panelu z telefonu: wybierz **Own final H3 prompt**, sześć rzeczywistych zdjęć,
co najwyżej jedno audio, batches1, jawny seed, krótki wspierany duration (np.5s)
i własny wolny tekst bez obowiązkowych sześciu sekcji. Najpierw **H3 Full**,
potem dopiero po completed/MP4 i zwolnieniu GPU **10Eros Full**. To dwa płatne
rendery; żaden nie wywołuje Prompt Workera. 20s, jeżeli wybrane zamiast5s,
zachowuje kontrakt **481 frames, 20.040s effective**. Nie zmieniamy defaults
0.4MPx/24steps/upscalerOFF/secondpassOFF ani jakości modeli Full.

Wariant API: po uploadach z [Gate3](GPU_PRODUCTION_SESSION.md#gate-3--prompt-only-dla-obu-profili-zero-render-post)
zachowaj ich odpowiedzi `upload-1.json` … `upload-6.json`, opcjonalny
`upload-audio.json` w prywatnym `AJ_SESSION_DIR`. Przed wynajmem przygotuj
własny `AJ_FINAL_FILE` UTF-8 i jawny `AJ_SEED`. Utwórz oba body raz, wysyłaj
**pojedynczo**; nie wykonuj części prompt-only Gate3, jeżeli wybrałeś tylko
ograniczony Personal First Run.

```bash
: "${AJ_SESSION_DIR:?prywatny katalog odpowiedzi uploadów}"
: "${AJ_FINAL_FILE:?własny finalny prompt UTF-8}"
export AJ_SESSION_DIR AJ_FINAL_FILE AJ_SEED
python3 - <<'PY_REQUEST'
import json, os, uuid
from pathlib import Path
p=Path(os.environ['AJ_SESSION_DIR'])
pictures=[json.loads((p/f'upload-{i}.json').read_text())['filename'] for i in range(1,7)]
audio_path=p/'upload-audio.json'
audio=[json.loads(audio_path.read_text())['filename']] if audio_path.exists() else []
for name, profile in [('h3-1','h3_full'), ('10eros-1','10eros_full')]:
    body=dict(request_id=str(uuid.uuid4()),profile=profile,prompt_mode='manual_final',
        manual_final_prompt=Path(os.environ['AJ_FINAL_FILE']).read_bytes().decode('utf-8'),
        pictures=pictures,audio=audio,batches=1,duration_seconds=5,
        seed=int(os.environ['AJ_SEED']),random_each=False)
    with (p/f'render-{name}.json').open('x') as f: json.dump(body,f,ensure_ascii=False)
PY_REQUEST
AJ_RENDER_CASE=h3-1  # Po completed/MP4 i zwolnieniu GPU: powtórz POST z10eros-1.
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error -H 'Content-Type: application/json' \
  --data-binary "@$AJ_SESSION_DIR/render-$AJ_RENDER_CASE.json" http://127.0.0.1:7860/api/batches \
  > "$AJ_SESSION_DIR/render-$AJ_RENDER_CASE-ack.json"
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error http://127.0.0.1:7860/api/jobs \
  > "$AJ_SESSION_DIR/jobs.json"
```

Przy timeout nie twórz nowego request_id: retry tylko identycznego
zapisanego body. `recovery/reconciliation_required` oznacza sprawdzenie history
znanego UUID, nie blind resubmit. H3/10Eros Full bez czarnych/statycznych klatek,
z właściwym audio, MP4 rzeczywiście odtwarza się przez OPEN MP4/Range i pobiera
na telefon. Sprawdź oba pobrane pliki poza instancją. OOM/wrong video → FAIL/STOP,
bez zamiany BF16 na INT8 lub Turbo.

Te dwa manual rendery + minimalny native test **nie dowodzą** pełnego Autoprompt,
overlap/recovery lub automatycznego Vast lifecycle. Normalny autoprompt pozostaje
dostępny; jego pierwsze użycie też obserwuj i licz pięć renderów na batch.
Dodatkowe acceptance wykonuj według potrzeby i osobnego budżetu, nie jako
certyfikację każdej zmiany seed/LoRA/duration. W razie błędu bieżącego writera
10Eros zatrzymaj jego autoprompting, zachowaj normalny error i prywatne logi.

## STOP i koniec rachunku

STOP przy przekroczeniu stage/global deadline, braku aktualnego billingu,
niezgodnym SHA/source/środowisku, braku miejsca lub nieudanej próbie. Zarezerwuj
czas/koszt cleanup **przed** ostatnim renderem; nie doprowadzaj salda do zera.
Panelowy licznik nie obejmuje wszystkich opłat, więc nie zastępuje billingu Vast.

1. Nie dodawaj pracy. Zatrzymaj własny foreground installer/probe; dla panelu
   wybierz STOP AFTER CURRENT tylko jeśli mieści się w deadline. Pobierz MP4 i
   potrzebne prywatne dowody na zaufane urządzenie, sprawdź je poza hostem.
2. Wyłącz submissions i zatrzymaj wyłącznie własne usługi istniejącym kontrolerem:

```bash
H3_ALLOW_SUBMISSIONS=0 "$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" write-env "$PANEL_ROOT/runtime.env"
bash "$PANEL_ROOT/scripts/service_ctl.sh" stop all
```

3. Potwierdź brak własnych procesów/GPU work. Nie stosuj ogólnego `pkill`/`killpg`
   ani sygnałów do zgadywanych PIDów. Nie używaj keep-data destroy: w ephemeral
   jest prawidłowo niedostępne bez prawdziwego verified Local Volume.
4. W **panelu Vast ręcznie DESTROY dokładnie tę instancję** po sprawdzeniu ID.
   STOP procesu/instancji nie jest dowodem ustania opłat storage.
5. Potwierdź w provider UI zniknięcie instancji i naliczania compute/dysku;
   sprawdź osobno brak volume i zatrzymanej płatnej instancji. Gdy wcześniej
   przypadkowo utworzono volume, po pobraniu danych usuń je osobno i sprawdź
   naliczanie. Zapisz końcowe rozbicie rachunku i cleanup w prywatnym ledgerze.

Na tym etapie możemy oceniać oferty hostów. Realne GO do zakupu wymaga oferty,
przepustowości, dostępnego dysku/RAM/cgroups i zatwierdzonego kosztorysu <=$15/4h.
Nie ma jeszcze wyniku GPU ani gwarancji jakości Full na Blackwell.
