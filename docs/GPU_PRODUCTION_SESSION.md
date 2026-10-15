# Jedna sesja H3 Full + 10Eros Full — Gate 0–6

Aktualizacja offline: punkt startowy PR #8
`baa59252d03ab553c7dc382ca6b4d0cdbdc5a36d` (976 testów CPU + 16 HTTP smoke
w CI). Zawiera Draft PR #5 `c6f033ab41a4456fd55558e652a563d8c31418d1`
i Draft PR #6 `84af26e26c0ec85a40efe712e5e32506c5a9af3f`; nie zakładaj merged
`main`. Do sesji wybierz finalny reviewowany SHA z zielonym CI, nie historyczny
punkt startowy dokumentu. Przygotowanie offline pozwala wybrać i zatwierdzić
host; **pełna produkcja nadal wymaga rzeczywistych Gate 1–6 i review 27B**.
Na obecnym kodzie DavidAU prawidłowo blokuje pełny installer. Nie uruchomiono
GPU, inference, downloadu wag ani żadnej akcji Vast.

Ten runbook rozszerza [kryteria Gate 1–6](GPU_ACCEPTANCE.md), a nie zamienia
wyników CPU w GPU PASS. [Inwentarz 22 artefaktów](PRODUCTION_MODEL_INVENTORY_2026-10-14.md)
zawiera exact sources, rewizje, bajty i pełne SHA256. Żadna poniższa komenda
GPU/provider nie została wykonana podczas przygotowania tego dokumentu.

## Gate 0 — dokumenty i decyzje przed pierwszym kosztem

Operator musi mieć prywatny, zreviewowany pakiet sesji; brak któregokolwiek
elementu oznacza STOP przed wynajmem:

| Element | Wymagane rozstrzygnięcie |
| --- | --- |
| Źródła | `AJ_NATIVE_SHA`: finalny reviewowany SHA PR #8 z zielonym CPU/CI; czysty checkout, ancestry PR #5/#6, Draft/open/unmerged i niezmieniona baza. Osobny `AJ_PROD_SHA` będzie zatwierdzony dopiero po dowodach 27B. Nie wpisuj dziś fikcyjnego SHA follow-upu. |
| Host | Jedna RTX PRO 6000 **Blackwell** 96 GB, CC 12.0; CUDA **13.0 devel**, driver >=580.65.06 i zgodny z dokładnym patchem toolkitu; Linux **>=6.9 oraz rzeczywisty probe flagi 4**. Host 6.8, nawet poprawny dla izolowanego Phase 1, NIE nadaje się do tej pełnej sesji. |
| RAM/CPU | Rzeczywiste affinity i cgroup limits, nie specyfikacja całego hosta. Planować 256 GiB efektywnie dostępnego RAM i >=16 vCPU; to rezerwa planistyczna, nie zmierzone minimum BF16. Operator zatwierdza progi i zapisuje ich wartości. `H3_BUILD_JOBS` <=8, <=efektywny CPU i <=dostępny RAM/3 GiB. |
| Storage | Z góry dobrany retained Local Volume na tej samej fizycznej machine co GPU, Docker instance; exact machine/volume/instance IDs, capacity, attachment i stawki. Planować **768 GiB + L** dla dwóch oddzielnych store, środowisk, buildów i wyników; L to dostarczone external LoRA. Sprawdzić zapas, nie polegać tylko na tej propozycji. |
| Wejścia | Sześć prywatnych obrazów z prawami użycia, jedno audio, scena i seedy. MysticXXX i Movement są domyślnie OFF; wymagaj ich zaufanych plików/SHA i enrollment tylko dla testu z ich jawnym włączeniem. Patrz [konfiguracja LoRA](LORA_CONFIGURATION.md). |
| Koszt/czas | Aktualna oferta, rzeczywisty billing i jego maksymalny wiek, saldo/top-up, compute/disk/volume/ingress/egress/retention, rezerwa retry, deadline każdego etapu i całej sesji. Zatwierdzony maksymalny USD i plan ręcznego provider STOP. Nie zakładać, że zatrzymanie procesu zatrzymuje billing. |
| Review 27B | Dostępny reviewer i ścieżka dostarczenia prywatnych dowodów; czas na review/testy/CI follow-upu. Jeśli review nie będzie gotowe w limicie, **STOP compute**, review offline; nie obiecujemy wtedy jednej nieprzerwanej płatnej sesji. |
| Dostęp | SSH tunnel/Basic Auth, brak publicznych 8188/8189; sekrety poza repo/logami. Potwierdzony dostęp/licencje wszystkich upstream repo. Zaufany operator może wykonać ręczne próby i późniejsze lifecycle; ta dokumentacja nie jest zgodą na wydatek. |
| Retencja/cleanup | Wybrane zewnętrzne miejsce prywatnego backupu i limit jego kosztu; z góry uzgodnione provider STOP/start/destroy oraz ostateczne **osobne usunięcie volume** po weryfikacji backupu/reattach. Zniszczenie compute nie usuwa Local Volume. |

Do prywatnego arkusza wpisz `PREFLIGHT_S`, `BUILD_S`, `DOWNLOAD_S`, `PROBE_S`,
`REVIEW_S`, `RENDER_S`, `SESSION_S`, `MAX_USD`, `BILLING_MAX_AGE_S`, `POLL_S`,
`TERM_GRACE_S`, rzeczywiste stawki i rezerwy. Żadnego domyślnego unlimited.
Wpisz też najwyższą liczbę płatnych renderów i koszt do końca Gate 6.
Przed każdym GO porównaj bieżący koszt z pozostałą rezerwą na dany gate i cleanup.
Brak aktualnego billingu/deadline, source drift lub osłabienie guardów = STOP.
Podczas raw prompt probes i instalacji produkcyjny cost guard nie nadzoruje
próby: operator obserwuje provider billing co `POLL_S`, z drugim alarmem deadline.
Nie używa depletion salda ani automatycznego DESTROY jako kill switch.

Produkcyjny panelowy cost guard używa **estymaty compute** `hourly_usd * czas od
startu kontrolera` z krótkim terminem ważności telemetry. Nie jest odczytem
faktury całej sesji, nie obejmuje transferu/retencji i po restarcie panelu jego
podstawa czasu zaczyna się od nowa. Dlatego wymagany zewnętrzny ledger rzeczywistego
billigu i total/session deadline działają przez **całe Gate1–6**, także podczas
restartów/reattach. `cost_guard_usd` dobierz do pozostałego budżetu tej epoki,
a nie ponownie do całego `MAX_USD`. Nie zmieniono istniejącej architektury guarda.

## Rachunek miejsca, czasu i kosztu

| Składnik | Bajty / ograniczenie |
| --- | --- |
| Pełny manifest, 22 artefakty: jawne `--include-optional` | **284661829208 B** = 284.662 GB = 265.112 GiB |
| Wymagane 17 artefaktów | **277909390584 B** |
| Domyślnie aktywne opcjonalne: HM Breasts V2 / Bunny Bridge | **77580016 B / 22045536 B** |
| Domyślna produkcja: 17 + 2 = 19 artefaktów | **278009016136 B** = 278.009 GB = 258.916 GiB |
| Oddzielny isolated store 11 GGUF | **113395969792 B** = 105.608 GiB |
| Pozostały download produkcji po zweryfikowanej kopii tych 11 | **164613046344 B** |
| Największy jednoplikowy staging (`h3-transformer`) | **66280487368 B** |
| Zimna domyślna produkcja + największy staging | **344289503504 B** |
| Dwa oddzielne store + największy staging, domyślnie | **457685473296 B** = 426.253 GiB |
| Dwa store + staging z całym bundle `--include-optional` | **464338286368 B** |

Liczby pochodzą z bieżącego `models_manifest.json`, wyboru 11 przez
`model_acceptance.load_selection()` oraz `default_lora_destinations()` używanego
przez CLI `provision_models.py`. HM jest domyślnie ON w H3, Bunny ON zgodnie z
rejestrem. Pozostałe opcjonalne wpisy manifestu nie są pobierane domyślnie.
Własne MysticXXX/Movement nie są częścią powyższego bundle: OFF nie wymagają
plików; włączenie wymaga tylko weryfikacji używanych zasobów, według
[LORA_CONFIGURATION.md](LORA_CONFIGURATION.md), bez reinstalacji stacku.

To **nie jest pełne wymaganie pojemności volume**. Dolicz rzeczywiste rozmiary
obu venv, osobnych checkoutów/buildów, HF/Xet/pip cache, zatrzymanych partials,
input/output/MP4, prywatnych dowodów/backupów i plików użytkownika L. Nie ma
gwarancji górnego limitu cache ani deduplikacji. Zewnętrzny staging całych GGUF
może dodać kolejne 113395969792 B. Plan **768 GiB + L** zostawia względem
samego domyślnego baseline **366948247536 B (341.747 GiB)** na te dodatki;
to rezerwa do sprawdzenia przez `df -B1` i `du -x -B1`, nie dowód wystarczalności.
Nie rezerwuj wszystkich wolnych bajtów dla jednego downloadera.

`provision_models.py --plan` domyślnie liczy wybrane 19; z `--include-optional`
liczy 22. Na obecnym manifeście drukuje plan, a potem kończy się błędem
wymaganego pending DavidAU: to prawidłowy STOP. Plan uwzględnia istnienie plików,
**nie potwierdza ich SHA**. Po poprawnej kopii 11 GGUF pokazuje dodatkowe minimum
wolnego miejsca **230893533712 B** (reszta + staging); zajęte już oba store nie
znikają z rachunku pojemności. Nie wyłączaj provenance dla szybszego startu.
Cache health ufa podpisowi destination dopiero po pełnym hash; kopiowanie receiptów
inode/source nie stanowi weryfikacji destination.

Przy kontrolowanym reuse na jednym volume zewnętrzny transfer wag wynosi
**113395969792 + 164613046344 = 278009016136 B**: GGUF pobierasz raz, potem
kopiujesz lokalnie. Sama kopia zapisuje dodatkowe 113395969792 B, a odczyt+zapis
to co najmniej 226791939584 B lokalnego I/O, przed hashami. Nie zakładaj zerowego
czasu/kosztu kopii, hardlinków ani deduplikacji. Przy innym hoście sprawdź także
billing transferu między store. Bez reuse download domyślnych wag wyniesie
**391404985928 B** (produkcja + isolated); dla wszystkich 22 odpowiednio
**284661829208 B z reuse / 398057799000 B bez reuse**. Nie doliczaj ponownie
pobranych GGUF do zewnętrznego transferu, jeśli faktycznie użyto poprawnej kopii.

Dolna granica zewnętrznego transferu domyślnych wag z reuse:
`8 * 278009016136 / (Mbit_s * 1e6)`:

| Faktyczny transfer | Sam transfer, bez narzutów/retry |
| --- | --- |
| 100 Mbit/s | 6.18 h |
| 250 Mbit/s | 2.47 h |
| 500 Mbit/s | 1.24 h |
| 1 Gbit/s | 0.62 h |
| 2 Gbit/s | 0.31 h |

Jeden odczyt SHA domyślnych 278.009 GB przy 250/500/1000 MB/s trwa co najmniej
18.5/9.3/4.6 min. Będzie kilka przebiegów (source, kopia, destination verification,
installer, preflight lub zmiana podpisu cache), a nie jeden. Zmierz przepustowość
rzeczywistego volume. Transfer image/source/packages, narzuty protokołu, retry,
backup i egress dolicz osobno; w powyższej sumie są wyłącznie wagi.

Robocza rezerwa, **nie benchmark ani obietnica**: host/attach 0.25–0.5 h,
transfer 0.6–2.5+ h, dwa środowiska/build/instalacje 0.75–2 h,
SHA/kopie 0.25–1 h, 11 native plików / wszystkie role vision / oba Step 3/4
1–3 h, review/cutover 0.5–2+ h. Render baseline jest nieznany.
Samo H3→10Eros→H3 przy 10/30/60 min na render to 0.5/1.5/3 h.
Gate 5 wymaga ponadto normalnych batchy (5 auto-renderów na profil, review),
overlap, sześć dodatkowych wariantów kontroli i recovery; planuj **co najmniej
23 rendery**, a lifecycle/LoRA/retry mogą zwiększyć tę liczbę. 23×30 min to
11.5 h samych renderów. Gate 6 z
ponownym startem/reattach i backupem wymaga osobnej rezerwy 0.5–2+ h.
Po pierwszym pomiarze przelicz resztę; jeśli nie mieści się w limicie, STOP,
a nie skracanie obowiązkowych kryteriów ani fałszywy PASS.

Przykłady samego compute, bez deklarowania ceny rynkowej:

| Czas | $1.50/h | $2.50/h | $4.00/h |
| --- | --- | --- | --- |
| 8 h | $12 | $20 | $32 |
| 12 h | $18 | $30 | $48 |
| 24 h | $36 | $60 | $96 |

Pełny ceiling: `H*(GPU/h + running_disk/h + volume/h) + retention_h*(stopped_disk/h
+ volume/h) + ingress_GB*$/GB + egress_GB*$/GB + external_preparation_USD`.
Transfer caps obejmują image/dependencies/retry/backup. Przelicz jednostki GB/GiB
z oferty. **$15/4h nie jest obecnie wiarygodnym budżetem pełnych gate'ów**;
nawet szybka sieć nie dowodzi czasu inferencji/renderów. Przed rental wymagaj
oferty i finansowania realistycznego wariantu z retry albo ogranicz cel do
osobno zatwierdzonego Gate 1A i jawnie pozostaw resztę NOT RUN.

## Pierwsze 15 minut po SSH — tylko bezpieczny preflight

Nie uruchamiaj installera, `--download`, pip/apt, buildów, usług ani inference.
Checkout, prywatny formularz i identyfikatory/progi przygotuj przed rental.
Wykonaj poniższy blok Gate 1A **tylko do `--phase A --inspect` włącznie**:

| Minuty | Czynność / GO albo STOP |
| --- | --- |
| 0–3 | Prywatny katalog dowodów, exact zatwierdzony SHA, czysty checkout i ancestry. Nie drukuj sekretów, formularza ani `runtime.env`. |
| 3–6 | Linux >=6.9 **i** `process_identity.py support` (realny `PIDFD_SIGNAL_PROCESS_GROUP`); UUID/model/96 GB/CC12, driver i CUDA13.0 zgodne z zatwierdzoną ofertą. `nvidia-smi`/`nvcc --version` nie uruchamiają modelu. |
| 6–10 | Zapisz affinity i wszystkie widoczne cgroup ancestors; porównaj efektywny RAM/vCPU z ofertą/progami, także ukryte limity potwierdzone przez providera. Sprawdź obce procesy GPU, porty i brak produkcyjnych usług. |
| 10–15 | `findmnt` + `df` i niezależny provider attachment: prawdziwy retained Local Volume, machine/volume IDs, wolne bajty i rezerwy. Potwierdź aktualny billing, stawki, deadline i ręczny STOP. `--inspect` musi PASS, zanim jakikolwiek download dostanie GO. |

Ten czas to kolejność pracy, nie obietnica PASS w 15 minut. Niezgodność, brak
dowodu lub niegotowy formularz = STOP przed pobieraniem; sam Linux6.9, nazwa
mountu czy `nvidia-smi` nie wystarczają. Wyjście `--inspect` zapisz prywatnie.

## Gate 1A — ten sam docelowy host, izolowane 11 GGUF

Przygotuj source checkout i prywatny katalog receipts na retained mount;
`AJ_SESSION_DIR` i wszystkie SHA/limity są wpisane wcześniej do arkusza.
Poniższe polecenia dotyczą **przyszłej, osobno autoryzowanej sesji**.

```bash
set -euo pipefail
umask 077
: "${AJ_NATIVE_SHA:?}" "${AJ_NATIVE_CHECKOUT:?}" "${AJ_SESSION_DIR:?}"
: "${H3_PERSISTENT_ROOT:?}" "${PREFLIGHT_S:?}" "${AJ_ACCEPTANCE_ROOT:?}"
install -d -m 700 "$AJ_SESSION_DIR"
export H3_MODEL_ACCEPTANCE_ROOT="$AJ_ACCEPTANCE_ROOT"
export HF_HOME="$AJ_ACCEPTANCE_ROOT/.hf-home"
export HF_XET_CACHE="$AJ_ACCEPTANCE_ROOT/.hf-xet"
export PIP_CACHE_DIR="$H3_PERSISTENT_ROOT/.private-pip-cache"
cd "$AJ_NATIVE_CHECKOUT"
test "$(git rev-parse HEAD)" = "$AJ_NATIVE_SHA"
test -z "$(git status --porcelain)"
git merge-base --is-ancestor c6f033ab41a4456fd55558e652a563d8c31418d1 HEAD
git merge-base --is-ancestor 84af26e26c0ec85a40efe712e5e32506c5a9af3f HEAD
python3 - <<'PY'
import platform, re
match = re.match(r'(\d+)\.(\d+)', platform.release())
assert platform.system() == 'Linux' and match and tuple(map(int, match.groups())) >= (6, 9)
PY
python3 h3/scripts/process_identity.py support
uname -a > "$AJ_SESSION_DIR/kernel.txt"
nvidia-smi --query-gpu=uuid,name,driver_version,memory.total,compute_cap --format=csv > "$AJ_SESSION_DIR/gpu.csv"
nvcc --version > "$AJ_SESSION_DIR/nvcc.txt"
findmnt -T "$H3_PERSISTENT_ROOT" -J -o SOURCE,TARGET,FSTYPE,FSROOT,UUID,MAJ:MIN > "$AJ_SESSION_DIR/mount.json"
df -B1 "$H3_PERSISTENT_ROOT" > "$AJ_SESSION_DIR/disk.txt"
nvidia-smi > "$AJ_SESSION_DIR/gpu-processes.txt"
ss -ltnp > "$AJ_SESSION_DIR/preflight-listeners.txt"
cat /proc/self/cgroup > "$AJ_SESSION_DIR/cgroup-membership.txt"
cat /proc/self/mountinfo > "$AJ_SESSION_DIR/mountinfo.txt"
python3 - <<'PY' > "$AJ_SESSION_DIR/cgroup-limits.json"
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
timeout --signal=TERM --kill-after=15s "$PREFLIGHT_S" python3 h3/scripts/gpu_acceptance_operator.py \
  --session "$AJ_SESSION_DIR/operator-session.json" --phase A --inspect \
  > "$AJ_SESSION_DIR/A-inspect-private.json"
```

GO dopiero gdy GPU/driver/nvcc/mount z dwóch źródeł zgadzają się z formularzem,
feature-probe zakończył się 0, cgroups spełniają zatwierdzony RAM/CPU, nie ma
obcych GPU procesów ani usług produkcyjnych. Zweryfikuj limity na każdym
widocznym poziomie, najmniejsze quota `cpu.max`/period i memory.max minus current
oraz affinity. Namespace może ukrywać wyższe limity: wymagaj także dowodu od
providera; samo `memory.max=max` nie jest poświadczeniem całego hosta.
Helper Phase 1 nie zastępuje kontroli produkcyjnego kernela ani cgroup ancestry.

Wykonaj B/C/D z [Phase 1](GPU_ACCEPTANCE_PHASE1.md) w osobnym terminalu, z jego source lock,
izolacją root/venv, pełnym SHA, rzeczywistym billingiem, monitorowaniem i deadline
każdej próby. W tamtym dokumencie `AJ_SESSION` oznacza plik JSON, a tutaj
`AJ_SESSION_DIR` katalog całej sesji; nie przenoś zmienionych zmiennych shell
między terminalami. Formularz Phase 1 to `$AJ_SESSION_DIR/operator-session.json`.
`INSTALL_MODEL_ACCEPTANCE.sh --install` buduje i pobiera, ale nie
wykonuje model probes. Ręczne komendy C obejmują wszystkie role 11 plików,
load/vision/minimal generation i oba writery w pełnych zatwierdzonych kontekstach.
Nie uznawaj `llama-cli` exit 0 za naturalne EOS. Dla 27B wymagaj raw native JSON,
reasoning/final, natural EOS/no truncation, Step 3 OFF / Step 4 ON, sześciu sekcji,
timeline 20 s→481 frames→20.040 s oraz minimal generation. Zachowaj VRAM/KV,
argv, input/SHA, driver/runtime, koszt i czas; wynik każdego case PASS/FAIL/NOT RUN.

**Punkt kontrolny review:** zakończ własne próby i potwierdź brak własnych
pozostawionych PID/VRAM. `TERM/KILL` + `timeout` nie gwarantują sprzątnięcia
zdetaszowanych potomków ani nie zatrzymują billing. Nie zabijaj obcych procesów.
Jeśli nie ma settled owner/result, STOP i ręczna interwencja operatora;
przekroczenie deadline lub brak review oznacza provider STOP compute.

## Przejście 1A → 1B — żadnego automatycznego wycofania DavidAU

1. Reviewer sprawdza rzeczywiste receipts, a nie deklarację operatora lub CPU test.
   Qwen3.5-27B load/reasoning/final/EOS/minimal generation muszą rzeczywiście PASS.
2. Osobny reviewowany follow-up wiąże dowody i zatwierdza retired gate. Zachowuje
   sources/SHA 22, Full BF16, runtime pins, v20 i guardy; nie fabrykuje metadata
   DavidAU ani wygenerowanego BF16 9B. Uruchamia pełny CPU/CI.
3. Dopiero wtedy wpisz zatwierdzony `AJ_PROD_SHA`. Zachowaj checkout native
   `AJ_NATIVE_SHA` i jego store/receipts osobno: `model_acceptance.load_selection`
   celowo wymaga oryginalnego retained gate i może odmówić działania po retirement.
4. Nowy produkcyjny checkout G2 nie nadpisuje frozen jobs/starego runtime. Na tym
   etapie produkcyjna kolejka musi być pusta; nie adoptuj starych identities.
5. Brak dowodów/approved SHA/CI = **STOP przed pełnym installerem**. Nie stosuj
   `allow_pending`, ręcznej edycji JSON na hoście ani fałszywego GPU PASS.

Obecny audyt NIE wykonuje punktu 2 i NIE otwiera DavidAU.

## Gate 1B — pełny stack na zatwierdzonym G2

```bash
: "${AJ_PROD_SHA:?brak reviewed cutover = STOP}" "${AJ_PROD_CHECKOUT:?}"
: "${BUILD_S:?}" "${DOWNLOAD_S:?}" "${TERM_GRACE_S:?}"
cd "$AJ_PROD_CHECKOUT"
test "$(git rev-parse HEAD)" = "$AJ_PROD_SHA"
test -z "$(git status --porcelain)"
export H3_PERSISTENCE_MODE=volume H3_CUDA_VERSION=13.0
export WORKSPACE="$H3_PERSISTENT_ROOT"
export COMFY_ROOT="$WORKSPACE/ComfyUI" PANEL_ROOT="$WORKSPACE/H3_VAST_MOBILE"
export COMFY_INPUT_DIR="$COMFY_ROOT/input" COMFY_OUTPUT_DIR="$COMFY_ROOT/output" COMFY_MODELS_DIR="$COMFY_ROOT/models"
export HF_HOME="$WORKSPACE/.production-hf-home" HF_XET_CACHE="$WORKSPACE/.production-hf-xet"
export H3_ALLOW_SUBMISSIONS=0 H3_ALLOW_DIAGNOSTIC_SUBMISSIONS=0 H3_ENABLE_TERMINAL=0
export H3_INSTALL_VAST_CLI=0
python3 h3/scripts/process_identity.py support
python3 h3/scripts/provision_models.py --manifest h3/config/models_manifest.json --models-root "$COMFY_MODELS_DIR" --plan
```

### Najpierw prawdziwy checkout ComfyUI, potem kopia GGUF

**Nie twórz samego `$COMFY_ROOT/models` przed checkoutem ComfyUI.** Pinned
comfy-cli1.21.0 widząc istniejący katalog pomija clone, a potem wymaga repo do
checkoutu wersji. Katalog zawierający wyłącznie modele nie spełnia tego warunku
([źródło `install.execute`](https://github.com/Comfy-Org/comfy-cli/blob/v1.21.0/comfy_cli/command/install.py)).
Przygotuj przypięty source; pełny installer obsługuje taki checkout przez
istniejącą ścieżkę `install --restore`. To pobiera kod, nie modele, i odbywa się
**dopiero po GO cutover**, poza checklistą pierwszych 15 minut:

```bash
AJ_COMFY_COMMIT="$(python3 h3/scripts/runtime_config.py constant COMFY_COMMIT)"
test ! -L "$COMFY_ROOT"
if [[ ! -e "$COMFY_ROOT" ]]; then
  timeout --signal=TERM --kill-after="$TERM_GRACE_S" "$BUILD_S" \
    git clone --no-checkout https://github.com/Comfy-Org/ComfyUI.git "$COMFY_ROOT"
  git -C "$COMFY_ROOT" checkout --detach "$AJ_COMFY_COMMIT"
fi
test -f "$COMFY_ROOT/main.py"
test "$(git -C "$COMFY_ROOT" rev-parse HEAD)" = "$AJ_COMFY_COMMIT"
git -C "$COMFY_ROOT" diff --exit-code HEAD -- > "$AJ_SESSION_DIR/comfy-source-diff.txt"
```

Nie naprawiaj obcego katalogu w miejscu. Inny SHA, symlink lub katalog models
bez repo = STOP; wybierz wcześniej uzgodniony czysty COMFY_ROOT, zachowując dane.
Wznowienie przerwanego clone wymaga sprawdzenia jego stanu przed checkoutem.
Przed kopią ponownie zweryfikuj isolated store **oryginalnym G1** (SHA/native
receipts; nie inference). Zmienne ścieżek mają wskazywać różne, rzeczywiste
katalogi na zatwierdzonym volume:

```bash
test "$(git -C "$AJ_NATIVE_CHECKOUT" rev-parse HEAD)" = "$AJ_NATIVE_SHA"
test -z "$(git -C "$AJ_NATIVE_CHECKOUT" status --porcelain)"
timeout --signal=TERM --kill-after="$TERM_GRACE_S" "$DOWNLOAD_S" \
  "$AJ_ACCEPTANCE_ROOT/.h3-venv/bin/python" "$AJ_NATIVE_CHECKOUT/h3/scripts/model_acceptance.py" \
  --root "$AJ_ACCEPTANCE_ROOT" --verify > "$AJ_SESSION_DIR/G1-before-copy-private.json"
AJ_NATIVE_CHECKOUT="$AJ_NATIVE_CHECKOUT" AJ_ACCEPTANCE_ROOT="$AJ_ACCEPTANCE_ROOT" \
  timeout --signal=TERM --kill-after="$TERM_GRACE_S" "$DOWNLOAD_S" \
  python3 - > "$AJ_SESSION_DIR/gguf-copy-private.json" <<'PY_COPY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['AJ_NATIVE_CHECKOUT'])
from h3.scripts.model_acceptance import load_selection
from h3.scripts.model_downloads import install_verified, store_lock
from h3.app.artifacts import verify_file
items, _ = load_selection()  # G1 retained gate, never the retired G2 selector
production = json.loads(Path('h3/config/models_manifest.json').read_text())
by_id = {item['id']: item for item in production['artifacts']}
keys = ('id', 'provider', 'repository', 'revision', 'repository_path', 'destination', 'size_bytes', 'sha256')
for item in items:
    assert all(by_id[item['id']][key] == item[key] for key in keys), 'STOP: G1/G2 artifact mismatch'
source = Path(os.environ['AJ_ACCEPTANCE_ROOT']) / 'models'
target = Path(os.environ['COMFY_MODELS_DIR'])
assert source.resolve() != target.resolve() and not source.resolve().is_relative_to(target.resolve())
assert not target.resolve().is_relative_to(source.resolve())
results = []
with store_lock(target):
    for item in items:
        verify_file(source, item['destination'], item)
        try:
            result = verify_file(target, item['destination'], item)
        except FileNotFoundError:
            result = install_verified(target, item, source, source / item['destination'])
        results.append({'id': item['id'], **result})
print(json.dumps(results, sort_keys=True))
PY_COPY
python3 h3/scripts/provision_models.py --manifest h3/config/models_manifest.json \
  --models-root "$COMFY_MODELS_DIR" --plan > "$AJ_SESSION_DIR/production-warm-plan.json"
df -B1 "$H3_PERSISTENT_ROOT" > "$AJ_SESSION_DIR/disk-before-install.txt"
```

To używa istniejącego atomowego publishera: pełny hash source, kopia zwykłych
bytes do prywatnego pliku, hash podczas kopiowania, fsync/no-overwrite, pełny
hash destination. Nie używa hardlinków ani kopiowania markerów/cache/receiptów.
Błędny istniejący destination = STOP, nie overwrite. Wznowienie tej samej
komendy pomija wyłącznie poprawnie zweryfikowane destination; przerwana kopia
bieżącego pliku może wymagać ponownego skopiowania tego pliku. Osierocone
`.partial` po twardym kill zajmują miejsce: po settled ownership uwzględnij je
w planie i usuń tylko dokładnie zidentyfikowane własne pliki. Nie wykonuj
równolegle provisionera ani nie zmieniaj źródeł podczas kopii.

Installer następnie rehashuje 11 destination, wystawia **nowe destination
receipts** i pobiera tylko brakującą resztę. HF SDK zachowuje resumable staging
`.downloads/<id>`; po przerwaniu instalacji sprawdź brak własnych dzieci,
source lock, mount, billing i nowy plan, po czym powtórz ten sam installer na
niezmienionym zatwierdzonym G2. Nie kasuj aktywnego staging/cache i nie ponawiaj
na nowych SHA w tej samej próbie. Błędne bytes istniejącego modelu nie wywołają
ślepego redownloadu/naprawy.

`HF_HOME` i `HF_XET_CACHE` są rozdzielone między acceptance i produkcję na
retained volume. Pip cache też ma jawną ścieżkę. Rzeczywisty rozmiar cache i
partials wchodzi w rezerwę; mały root disk nie jest stagingiem dla wag.

Teraz, dopiero po zatwierdzonym cutover, pojedynczy installer:

```bash
: "${H3_BUILD_JOBS:?zatwierdzony limit CPU/RAM}"
unset COMFY_PYTHON PYTHON_BIN
# python_env wybierze produkcyjny WORKSPACE/.h3-venv, nie isolated runtime.
timeout --signal=TERM --kill-after="$TERM_GRACE_S" "$((BUILD_S + DOWNLOAD_S))" \
  bash h3/INSTALL_ON_VAST.sh > "$AJ_SESSION_DIR/provision.log" 2>&1
unset HF_TOKEN HF_HUB_TOKEN HUGGING_FACE_HUB_TOKEN
set -a
source "$PANEL_ROOT/runtime.env"
set +a
test "$H3_ALLOW_SUBMISSIONS" = 0
test "$H3_ENABLE_TERMINAL" = 0
export H3_ALLOW_DIAGNOSTIC_SUBMISSIONS=0
```

Przed installerem wymagaj wcześniej zatwierdzonego Vast CLI: zapisz jego
wersję, pochodzenie i SHA. `H3_INSTALL_VAST_CLI=0` wyłącza dynamiczny `curl|bash`
instalatora; brak zatwierdzonego CLI blokuje późniejsze lifecycle. Nie pobierano
ani nie uruchamiano tego CLI w audycie CPU.
HF token dostarcz poza argv/history jednorazowemu procesowi; nie loguj runtime.env.
Installer **uruchamia oba ComfyUI i panel na końcu**, pozostawiając submissions
OFF. Nie jest wyłącznie downloadem. Runtime interpreter jest jeden, Python >=3.11,
ComfyUI 0.38.0 commit `6b747c0428c343e1417219641db93a4fb7cb69ae`, comfy-cli1.21.0,
llama/node pins jak Gate1A, CUDA arch120. Pozostałe registry nodes rozwiązuje
`comfy node install-deps --uv-compile`; zachowaj realny resolved inventory i
`pip freeze` po instalacji. CPU nie dowodzi zgodności tych upstream zależności.
Nie stosuj `latest`/auto-upgrade po rozpoczęciu acceptance.

### Opcjonalny pełny bundle i późniejsze LoRA

Zwykły `INSTALL_ON_VAST.sh` wybiera wymagane + domyślnie aktywne optional,
a nie wszystkie 22. `--include-optional` jest flagą **provision_models.py**,
nie wrappera instalacji. Tylko jeśli świadomie potrzebujesz pełnego bundle,
po prawidłowej instalacji, przy submissions OFF i sprawdzonym planie/budżecie:

```bash
AJ_LLAMA_SOURCE="${H3_BUILD_ROOT:-$WORKSPACE/.h3-build}/llama.cpp-$("$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" constant LLAMA_TAG)"
"$WORKSPACE/.aj-download-venv/bin/python" "$PANEL_ROOT/scripts/provision_models.py" \
  --manifest "$PANEL_ROOT/config/models_manifest.json" --models-root "$COMFY_MODELS_DIR" \
  --include-optional --plan > "$AJ_SESSION_DIR/all-optional-plan.json"
# Token, jeśli potrzebny: poza argv/history, tylko dla tego procesu; nie set -x.
timeout --signal=TERM --kill-after="$TERM_GRACE_S" "$DOWNLOAD_S" \
  "$WORKSPACE/.aj-download-venv/bin/python" "$PANEL_ROOT/scripts/provision_models.py" \
  --manifest "$PANEL_ROOT/config/models_manifest.json" --models-root "$COMFY_MODELS_DIR" \
  --toolchain "$AJ_LLAMA_SOURCE" --include-optional > "$AJ_SESSION_DIR/all-optional-provision.json"
unset HF_TOKEN HF_HUB_TOKEN HUGGING_FACE_HUB_TOKEN
```

Ten krok nie włącza LoRA w profilu, nie instaluje external MysticXXX/Movement
i nie omija DavidAU. Nie jest potrzebny przy domyślnych ustawieniach.

Opcjonalnie, tylko jeśli sesja obejmuje jawnie włączone MysticXXX i Movement:
umieść zaufane external LoRA bez overwrite, porównaj dostarczone pełne SHA i
rozmiary, następnie enroll te ID (nie poświadczy to upstream provenance ani GPU
compatibility). Pomiń ten krok przy domyślnych ustawieniach:

```bash
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/enroll_external_loras.py" \
  --models-root "$COMFY_MODELS_DIR" --registry "$PANEL_ROOT/config/lora_registry.json" \
  mysticxxx-v4 movement-v1
```

Nie uruchamiaj enrollment ponownie dla zmienionych bytes. Bunny bundled shadow
także musi pasować do manifestu; błędny lub obcy plik = STOP, nie zaakceptowanie
zastępstwa. H3: HM 1.0 ON, MysticXXX 0.6 OFF, Movement 0.5 OFF; 10Eros LoRA
domyślnie OFF. Pliki wyłączonych adapterów nie są warunkiem wykonania.

## Gate 2 — infrastruktura i brak submissions

```bash
bash "$PANEL_ROOT/scripts/preflight.sh" > "$AJ_SESSION_DIR/preflight.log" 2>&1
bash "$PANEL_ROOT/scripts/smoke_test.sh" > "$AJ_SESSION_DIR/smoke.log" 2>&1
curl --fail --silent --show-error http://127.0.0.1:8188/object_info > "$AJ_SESSION_DIR/render-object-info.json"
curl --fail --silent --show-error http://127.0.0.1:8189/object_info > "$AJ_SESSION_DIR/prompt-object-info.json"
ss -ltnp > "$AJ_SESSION_DIR/listeners.txt"
"$COMFY_PYTHON" -m pip freeze > "$AJ_SESSION_DIR/pip-freeze.txt"
```

GO: oba PASS; wszystkie native/fallback/LLM/loader/video classes obecne; cztery
przygotowane grafy obu profili/phases przechodzą realny catalog/final validator;
żadnego POST `/prompt`; oba worker history/queue puste; panel authenticated,
nieautoryzowany `/api/config` daje401; publiczne 8188/8189 niedostępne z drugiego
hosta. Loopback listener i provider firewall sprawdzić niezależnie. Terminal
**niedostępny**, także przy env1 — nie włączaj eksperymentalnego PTY.
1px smoke assets nie zastępują sześciu rzeczywistych referencji w Gate3/4.

Utwórz prywatny curl config `AJ_CURL_CONFIG` z Basic Auth poza logami i argv.
Przykład (dane wprowadzane na prywatnym terminalu, nie w argv):

```bash
: "${AJ_CURL_CONFIG:?nowy prywatny plik}"
python3 - "$AJ_CURL_CONFIG" <<'PY'
import getpass, json, os, sys
user = input('Panel user: ')
password = getpass.getpass('Panel password: ')
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write('user = ' + json.dumps(user + ':' + password, ensure_ascii=False) + '\n')
PY
```

Dalsze wywołania panelu używają `curl --config "$AJ_CURL_CONFIG"`. Brak poprawnej
autoryzacji nie jest powodem wyłączenia auth.

## Gate 3 — prompt-only dla obu profili, zero render POST

W panelu/API prześlij 6 referencji i audio. Każde upload zwraca bezpieczny nowy
filename; zapisz odpowiedzi w prywatnym evidence. Dokładny request:

```bash
for AJ_I in 1 2 3 4 5 6; do
  AJ_REF_VAR="AJ_REFERENCE_$AJ_I"
  test -f "${!AJ_REF_VAR}"
  curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error \
    -F "file=@${!AJ_REF_VAR}" http://127.0.0.1:7860/api/upload > "$AJ_SESSION_DIR/upload-$AJ_I.json"
done
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error \
  -F "file=@$AJ_AUDIO" http://127.0.0.1:7860/api/upload > "$AJ_SESSION_DIR/upload-audio.json"
```

Przygotuj prywatne `prompt-h3.json` i `prompt-10eros.json`: `request_id` (różny
kanoniczny UUID), `profile`, `prompt_mode:"autoprompt"`, `prompt` z rzeczywistą
sceną, `pictures` z6 zwróconych filenames, `audio` z0/1 filename, `batches:1`,
`duration_seconds:20`, `seed:12345`, `random_each:false`. Nie podawaj render
settings, aby użyć niezmienionych defaults. Te pliki **nie trafiają do /api/batches**.

```bash
: "${AJ_SCENE_FILE:?prywatny plik UTF-8 ze sceną}"
python3 - "$AJ_SESSION_DIR" "$AJ_SCENE_FILE" <<'PY'
import json, sys, uuid
from pathlib import Path
p = Path(sys.argv[1])
pictures = [json.loads((p / f'upload-{i}.json').read_text())['filename'] for i in range(1, 7)]
audio = [json.loads((p / 'upload-audio.json').read_text())['filename']]
for name, profile in [('h3', 'h3_full'), ('10eros', '10eros_full')]:
    body = dict(request_id=str(uuid.uuid4()), profile=profile, prompt_mode='autoprompt',
                prompt=Path(sys.argv[2]).read_bytes().decode('utf-8'), pictures=pictures,
                audio=audio, batches=1, duration_seconds=20, seed=12345, random_each=False)
    with (p / f'prompt-{name}.json').open('x') as f:
        json.dump(body, f, ensure_ascii=False)
PY
```

```bash
export H3_ALLOW_SUBMISSIONS=0 H3_ALLOW_DIAGNOSTIC_SUBMISSIONS=0
AJ_CASE=h3  # Po settled PASS powtórz blok z AJ_CASE=10eros, nie wcześniej.
AJ_PROMPT_OUTPUT="$AJ_SESSION_DIR/probe-$AJ_CASE"
AJ_PROMPT_UUID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["request_id"])' "$AJ_SESSION_DIR/prompt-$AJ_CASE.json")"
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/prepare_prompt_acceptance.py" \
  --request "$AJ_SESSION_DIR/prompt-$AJ_CASE.json" --output "$AJ_PROMPT_OUTPUT" \
  --execution-id "$AJ_PROMPT_UUID" --analysis-seed 24680 --prompt-seed 67890
sha256sum "$AJ_PROMPT_OUTPUT/request.json" > "$AJ_PROMPT_OUTPUT/request.sha256"
```

Eksporter tylko GET `/object_info` i nonsubmitting comfy conversion; weryfikuje
strict production manifest/files/native przed i po przygotowaniu, zapisuje
frozen context/request/SHA0600 w nowym katalogu. Nie publikuje batcha, nie
uruchamia startup/controller workers, nie kontaktuje Render POST. Obecny
DavidAU pending blokuje go; nie jest alternatywnym allow_pending.

Operator sprawdza worker queues puste, freeze SHA/profile/seeds, realny billing,
deadline i spodziewany wyłącznie prompt graph. Nie edytuje sources/modeli ani
grafu między verification i POST. Po osobnym GO dokładnie jeden manualny POST:

```bash
curl --silent --show-error --max-time 30 -H 'Content-Type: application/json' \
  --data-binary "@$AJ_PROMPT_OUTPUT/request.json" http://127.0.0.1:8189/prompt \
  > "$AJ_PROMPT_OUTPUT/ack.json"
python3 - "$AJ_PROMPT_OUTPUT/ack.json" "$AJ_PROMPT_UUID" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
assert value.get('prompt_id') == sys.argv[2] and not value.get('error') and not value.get('node_errors')
PY
curl --fail --silent --show-error "http://127.0.0.1:8189/history/$AJ_PROMPT_UUID" \
  > "$AJ_PROMPT_OUTPUT/history.json"
curl --fail --silent --show-error "http://127.0.0.1:8189/aj/results/$AJ_PROMPT_UUID" \
  > "$AJ_PROMPT_OUTPUT/journal.json"
```

History początkowo może być puste; GET history/queue/journal powtarzaj do
settled successful result, najwyżej do `PROBE_S` i session deadline. **POST nie
ma retry**. Timeout/503/invalid JSON/mismatch UUID = uncertain; zachowaj jedyny
UUID, sprawdź `/queue`, `/history/{uuid}`, `/aj/results/{uuid}`. Brak dowodu
settlement oznacza STOP/reconciliation, nigdy nowy UUID/blind POST.
Gdy upłynie czas próby, anuluj tylko własne UUID przez `/aj/cancel/{uuid}`,
potwierdź terminal journal i brak running/pending; jeśli to niemożliwe, wstrzymaj
sesję, nie przechodź do renderu. Procedura ręczna nie dziedziczy panelowego
watchdoga/cost guard; to obowiązek operatora, nie ukryty automat.

Zapisz final i canonical plan przez istniejący parser, bez przepisywania tekstu:

```bash
"$COMFY_PYTHON" - "$PANEL_ROOT" "$AJ_PROMPT_OUTPUT" "$AJ_PROMPT_UUID" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app import main as m
p = Path(sys.argv[2]); pid = sys.argv[3]
entry = json.loads((p / 'history.json').read_text())[pid]
assert m._history_outcome(entry) == 'success'
texts = m.extract_prompt_texts(entry)
assert all(isinstance(value, str) and value.strip() for value in texts.values())
context = json.loads((p / 'evidence.json').read_text())['job']['context']
from app.temporal import final_timeline
final_timeline(texts['final_h3_prompt'], texts['creative_plan'], context['effective_duration_seconds'])
(p / 'final.txt').write_bytes(texts['final_h3_prompt'].encode('utf-8'))
(p / 'texts.json').write_text(json.dumps(texts, ensure_ascii=False, indent=2))
PY
```

Powtórz dla 10Eros przez `AJ_CASE=10eros`, z zapisanym osobnym UUID/output.
GO: Step0–4 i native EOS/no truncation receipts, Step3 OFF/Step4 ON, guarded
final 6 sections/timeline, profile-specific models i budgets z GPU_ACCEPTANCE,
oba journal settled, **zero POST8188**. Zachowaj native receipt files/logs,
nie tylko Preview text. Cold/warm cache i service-epoch dowodzi dopiero Gate5
przez kontroler; dwa izolowane eksporty nie poświadczają cache batchy.

## Gate 4 — dokładnie H3 → 10Eros → H3

Włącz produkcyjne submissions dopiero z pustą kolejką, po Gate2/3 i aktualnym
billigu. Zapisz runtime przez istniejący literal writer, restartuj tylko panel:

```bash
H3_ALLOW_SUBMISSIONS=1 "$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" write-env "$PANEL_ROOT/runtime.env"
bash "$PANEL_ROOT/scripts/service_ctl.sh" restart panel
```

Z pustą kolejką skonfiguruj panelowy `set_cost_guard` i jawne
`resume_cost_guard` według Gate6, przy świeżej telemetry i zewnętrznym actual
billing ledger. Do tego momentu nie twórz jobów. Włączony guard pozostaje
fail-closed po restarcie do ponownej świadomej kontroli/resume.

Twórz **manual_final, batches1** z exact `final.txt` odpowiedniego profilu z Gate3,
6 rzeczywistymi refs i audio. Scene prompt pozostaje osobno; final w JSON
generuj odczytem pliku UTF-8, nie shell command substitution (usuwa końcowe LF).
`request_id` ustal raz i zapisz body na dysku przed POST. Zostaw render_settings
pominięte, nie zmieniaj defaults. Obecne wartości to **0.4 MPx, 24 kroki,
latent upscaler OFF, drugi pass OFF**. Nieaktywne wartości to upscaler 1.2 MPx
i drugi pass 4 kroki, second-pass LoRA puste.
Przykład utworzenia z przygotowanego requestu:

```bash
"$COMFY_PYTHON" - "$AJ_SESSION_DIR" <<'PY'
import json, sys, uuid
from pathlib import Path
p = Path(sys.argv[1])
for case, source in [('h3-1', 'h3'), ('10eros-1', '10eros'), ('h3-2', 'h3')]:
    body = json.loads((p / f'prompt-{source}.json').read_text())
    body.update(request_id=str(uuid.uuid4()), prompt_mode='manual_final',
                manual_final_prompt=(p / f'probe-{source}/final.txt').read_bytes().decode('utf-8'), batches=1)
    with (p / f'render-{case}.json').open('x') as f: json.dump(body, f, ensure_ascii=False)
PY
AJ_RENDER_CASE=h3-1  # Dopiero po completed/settled: 10eros-1, następnie h3-2.
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error -H 'Content-Type: application/json' \
  --data-binary "@$AJ_SESSION_DIR/render-$AJ_RENDER_CASE.json" http://127.0.0.1:7860/api/batches > "$AJ_SESSION_DIR/render-$AJ_RENDER_CASE-ack.json"
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error http://127.0.0.1:7860/api/jobs > "$AJ_SESSION_DIR/jobs.json"
```

Na timeout API batches można ponowić **identyczne zapisane body/request_id**,
nie tworzyć nowego requestu. Kontroler musi zwrócić oryginalne batch ID.
Panel/detail każdego job pokazuje remote UUID. Poczekaj na completed+settled
history+MP4 przed kolejnym requestem: 10Eros manual z jego final, potem nowe H3
manual z tym samymH3 final i jawnie zapisanym seedem. To trzy płatne rendery,
nie zwykły batch zpięcioma auto. Manual mode nigdy nie uruchamia Prompt Worker.

W każdej próbie próbkuj `nvidia-smi` ztimestamp np.co1s, logi obu workerów,
przejścia conditioning/sampling/decoding, peak VRAM i RAM/cgroup. Wymagaj
zwolnienia/offload encoder przed late transformer, tylko1 heavy render,
brak obu heavy modeli naraz i brak per-step weight streaming/OOM/restart loop.
Obejrzyj MP4 i wyodrębnione klatki: czarne/statyczne/uszkodzone = FAIL,
nie powód podmiany Full na INT8/Turbo. Dla output zapisz SHA, ffprobe frames,
FPS, duration, audio, rozdzielczość, seed, checkout/model identities i wall time.

```bash
ffprobe -v error -count_frames -show_entries stream=index,codec_type,width,height,r_frame_rate,nb_read_frames,duration \
  -show_entries format=duration -of json "$AJ_MP4" > "$AJ_SESSION_DIR/ffprobe.json"
ffmpeg -v error -i "$AJ_MP4" -f null -
sha256sum "$AJ_MP4" > "$AJ_SESSION_DIR/mp4.sha256"
```

20srequest→481frames; timeline20.040s, 481/24≈20.041667s — nie zaokrąglaj
frozen timeline do request. MP4 przez authproxy OPEN MP4 musi się odtwarzać;
Range GET z `Range: bytes=0-1023` daje206/Content-Range, a bez auth401.
Sprawdź wpływ wszystkich6 refs, audio i LoRA; sam brak błędu loadera nie dowodzi
zgodności LoRA. Dodatkowe kontrolowane A/B LoRA wymagają oddzielnego slotu w
budżecie; nie zmieniaj defaults/config w locie ani już zamrożonych jobów.

Po trzech baseline PASS wykonaj dodatkowo trzy warianty **dla każdego profilu**:
upscaler ON / drugi pass OFF, OFF/ON i ON/ON. OFF/OFF jest już baseline.
To sześć dodatkowych płatnych renderów. Testujemy ustawienia poszczególnych
requestów, nie zmieniamy konfiguracji ani defaults. Przy drugim pass ON użyj
zweryfikowanego `hm-breasts-v2` ze strength 1.0, aby sprawdzić również tę gałąź
LoRA. Pozostałe opcjonalne LoRA bez własnego GPU testu zachowują status pending.

```bash
"$COMFY_PYTHON" - "$AJ_SESSION_DIR" <<'PY'
import json, sys, uuid
from pathlib import Path
p = Path(sys.argv[1])
for profile, baseline in [('h3', 'h3-1'), ('10eros', '10eros-1')]:
    for upscaler, second in [(True, False), (False, True), (True, True)]:
        body = json.loads((p / f'render-{baseline}.json').read_text())
        body['request_id'] = str(uuid.uuid4())
        body['render_settings'] = dict(latent_upscaler_enabled=upscaler, second_pass_enabled=second,
            second_pass_loras=[dict(id='hm-breasts-v2', enabled=True, strength=1.0)] if second else [])
        name = f'render-{profile}-controls-{int(upscaler)}{int(second)}.json'
        with (p / name).open('x') as f: json.dump(body, f, ensure_ascii=False)
PY
```

Wysyłaj pojedynczo według wzorca `AJ_RENDER_CASE`, w kolejności
`h3-controls-10`, `h3-controls-01`, `h3-controls-11`, potem te same trzy
`10eros-controls-*`. Każdy musi completed/settled i przejść MP4/VRAM/graph
checks przed następnym. Sprawdź zapisane frozen controls, rzeczywiste wykonanie
dekodera właściwego latent, padding, obrazy/audio CreateVideo i output.
Brak PASS wariantu oznacza nieprzyjętą funkcję; nie ukrywaj tego wynikiem baseline.

## Gate 5 — kontroler, overlap, recovery i panel

Każdy test ma zapisaną liczbę requestów, UUID, deadline i kryterium STOP.
Normalny Autoprompt batch (ten sam request schema co Gate3, ale wysłany do
`POST /api/batches`) tworzy10 candidates i **5 auto płatnych renderów**, dlatego
wolno go uruchomić dopiero teraz. Wykonaj osobno H3 i10Eros, nie więcej niż
zatwierdzony limit. Review approval doda kolejny render.

| Próba / polecenie lub UI | PASS / STOP |
| --- | --- |
| Oba profile Autoprompt10=5 auto+5 review | Step0–4, osobne seeds, cold/warm Step0 shared i Step1/2 rozdzielone; candidates2–10 reuse zweryfikowanej analizy; żadnego drugiego autopromptera na Render Worker. Review bez approval nie renderuje. |
| Edytuj i zatwierdź jeden Review Pool | `POST /api/jobs/{id}/approve`, body `final_prompt` exact; hash/temporal checks; approved review ma priorytet, retry approval nie duplikuje renderu. Reject innego review nie dispatchuje. |
| Warm writer podczas samplingu | Porównaj z Gate4baseline; current progi H3writer24576MiB,10Eros40960MiB; coldStep0–2 samplingOFF obu. Zmierz freeVRAM/KV i realne przedziały admit/hold, prefetch3 bounded i jeden render. Conditioning/decoding/recovery/unknown zamykają okno przed GPU alloc, aktywny LLM kończy/reapuje. Bez SIGSTOP CUDA. Nie dostrajaj progów dla PASS. |
| Diagnostics/Live Logs/Queue/Results | `/api/diagnostics`, `/api/logs/render`, `/api/logs/prompt`, `/api/logs/panel`, `/api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/prompts`; health/storage/cost/prefetch widoczne, brak nieautoryzowanego dostępu; results/Range i timing po restarcie. Terminal backend/UI pozostaje disabled. |
| Restart idle Prompt Worker | `POST /api/services/prompt/restart`; nowy epoch, verified durable texts pozostają, nie zakładamy GPU cache. Przy active job najpierw cancel/settle; krótkiego błędu health nie zamieniaj w lost ownership. |
| Restart idle Render Worker | `POST /api/services/render/restart`; exact owned PID/process group/quiesce/settlement, port wraca i native manifest nie zmienia się. Nowy test nie zaczyna się przy starym uncertain UUID. |
| Restart panelu przy disposable accepted job | `bash "$PANEL_ROOT/scripts/service_ctl.sh" restart panel`; recovery sprawdza ten samremote UUID, kolejka iMP4 zachowane, zero drugiego POST. Nie kasuj queue/journal dla „naprawy”. |
| Cancel/watchdog | `POST /api/jobs/{id}/cancel`; disposable job z zatwierdzonym krótkim `soft_timeout_minutes`/`hard_restart_after_seconds`, cancellation i settled/restart ownership. Nie zabijaj cudzych procesów ani nie edytuj durable IDs. |
| Lost ACK /503/invalid JSON /arm crash /persistence failure | CPU suite symuluje te granice i wymaga at-most-one POST. Ten runbook nie wstrzykuje takich awarii w płatny stack. CPU wynik raportuj oddzielnie; live fault injection pozostaje NOT RUN, bez deklaracji GPU PASS dla tych dodatkowych scenariuszy. Nie improwizuj firewall/kill/disk-full na live state. |

Panel restart commands używają aktualnego kontraktu API; zachowaj auth i zapisuj
status/body. Niedostępny endpoint/409/503 to STOP/reconciliation, nie retry w
pętli. Rozdziel CPU-dowód scenariuszy awarii od faktycznego testu na GPU.
Obowiązkowe live recovery obejmuje powyższe restarty, cancel/watchdog i retencję.
Dodatkowy destructive fault injection wymaga osobnego przygotowanego fixture;
nie jest domyślną częścią płatnej sesji ani dowodem uzyskanym przez CPU.

## Gate 6 — live lifecycle, retencja i koniec billingu

Przed destructive actions: backup poza instancję, checksum, realny provider
attachment i `H3_PERSISTENT_VOLUME_PROOF` według [h3/README](../h3/README.md).
Dowód musi zawierać current instance/boot/mount/dev/inode i rzeczywisty retained
volumeID. Nie generuj PASS tylko z`findmnt`. `/api/diagnostics` ma pokazać
safe_for_destroy_keep_data; wszystkie protected paths na tym samym volume.

Każda decyzja lifecycle używa świeżego `control_token` z`GET /api/vast/status`.
Body `POST /api/vast/action` ma `action`, `control_token` i dla stop_now
`confirm:"STOP"`, dla destroy_now `confirm:"DESTROY"`. Zapisz request przed
wysłaniem. Stale409 oznacza refresh i nową świadomą decyzję, nie blind retry.
STOP/start/destroy compute w tej tabeli są **przyszłymi płatnymi akcjami**, tutaj
niewykonanymi. Provider start i potwierdzenie stanów wykonuje operator w console
konkretnej instancji; nie zgadujemy flag wersji Vast CLI.

Ten sam dokładny wzorzec stosuj pojedynczo do wcześniej wybranego działania
z tabeli. Prywatny `AJ_ACTION_PARAMETERS` zawiera już zatwierdzone `action`
i odpowiednie `confirm` / `idle_minutes` / `cost_guard_usd`; nie zawiera tokena.
`AJ_ACTION_REQUEST` jest nowym plikiem dla jednej decyzji, nie dla pętli retry.

```bash
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error \
  http://127.0.0.1:7860/api/vast/status > "$AJ_SESSION_DIR/lifecycle-status.json"
python3 - "$AJ_SESSION_DIR/lifecycle-status.json" "$AJ_ACTION_PARAMETERS" "$AJ_ACTION_REQUEST" <<'PY'
import json, sys
status = json.load(open(sys.argv[1]))
body = json.load(open(sys.argv[2]))
assert body['action'] in {'cancel_plan', 'set_idle_timer', 'set_cost_guard', 'resume_cost_guard',
    'stop_after_current', 'stop_after_queue', 'destroy_after_queue_keep_data', 'stop_now', 'destroy_now'}
assert 'control_token' not in body
body['control_token'] = status['control_token']
with open(sys.argv[3], 'x') as f: json.dump(body, f)
PY
curl --config "$AJ_CURL_CONFIG" --fail --silent --show-error -H 'Content-Type: application/json' \
  --data-binary "@$AJ_ACTION_REQUEST" http://127.0.0.1:7860/api/vast/action > "$AJ_ACTION_REQUEST.response.json"
```

| Kolejność / action | Wymagany dowód i GO kolejnego kroku |
| --- | --- |
| `stop_after_current` z jednym disposable active ijednym queued | Brak nowego preparation/POST, aktualny job settled, provider stopped. Start tej samej instancji; onstart/ownedworkers/queue/MP4 i control generation poprawne. |
| `stop_after_queue` zmałą skończonąqueue | Auto/approved drain; unsubmitted review preparation pomijane, pending/rejected review nie blokuje; provider stopped, ponowny start bez duplikacji. |
| `set_idle_timer` (`idle_minutes`) i `set_cost_guard` (`cost_guard_usd`), potem `resume_cost_guard` | Zewnętrzny actual billing jest aktualny; panelowa estymata compute/telemetry jest fresh/valid/below limit. Missing/invalid telemetry zamyka admission. Idle stop i przekroczenie zatwierdzonego limitu tej epoki skutkują bezpiecznym hold/stop. Zostaw rezerwę cleanup, nie wyłączaj guarda aby wymusić GO. |
| `stop_now` z disposable job | Cancel/quiesce/owned cleanup i providerSTOP; start, recovery tego samego UUID, zachowane dane. |
| `destroy_after_queue_keep_data` | Tylko verified retainedvolume i backup; queue drained, destroy compute nievolume. Nowy compute na **tej samej machine**, ten samLocalVolume, reverifyattachment i nowy proof, onstart/bootstrap source G2 bez model redownloadu i bez duplikacji. To kolejny billing/instanceID w limicie sesji. |
| `destroy_now` na ostatniej disposable instancji | Jawne DESTROY, backup gotowy, exact instance ID; po action provider potwierdza compute destroyed. Nie używaj tej akcji do „testu”, gdy nie ma planu odzyskania danych. |
| Ostateczny cleanup | Po sprawdzeniu prywatnego backupu/receiptów i retencji/reattach operator **osobno usuwa dokładny LocalVolumeID wprovider console** i potwierdza brak volume/stopped instances/unattached disks/active compute. Zachowaj invoice i czas zakończenia każdej pozycji billingu. Brak potwierdzenia usunięcia = dalszy kosztretencji, nie „sesja zakończona”. |

Nie ma komendy projektu usuwającej volume. Dokument nie wymyśla niezweryfikowanej
komendy `vastai delete-volume`. Provider UI/API i bieżące ceny/deadlines muszą
być sprawdzone przed sesją. Zgoda na keep-data destroy NIE jest zgodą na późniejsze
usunięcie volume; operator stosuje uprzednio zatwierdzony plan cleanup.

Jeżeli sesja kończy się ręcznym STOP providera poza działającym lifecycle panelu,
po anulowaniu/settlement własnych jobów i backupie zatrzymaj własne usługi:

```bash
bash "$PANEL_ROOT/scripts/service_ctl.sh" stop all
bash "$PANEL_ROOT/scripts/service_ctl.sh" status all > "$AJ_SESSION_DIR/services-stopped.txt"
nvidia-smi > "$AJ_SESSION_DIR/gpu-before-provider-stop.txt"
ss -ltnp > "$AJ_SESSION_DIR/listeners-before-provider-stop.txt"
```

Nie wykonuj tego przed zaplanowaną akcją API, która potrzebuje działającego panelu.
Powyższe nie obejmuje raw native probe/build dzieci: ich zakończenie sprawdza
operator według Phase1, bez `pkill` na cudzych procesach. Błąd ownership/stop
oznacza STOP i ręczną interwencję, nie stwierdzenie „GPU wolne”. Zatrzymanie
procesów **nie zatrzymuje billingu**: potem wymagaj potwierdzonego provider STOP
lub destroy compute i osobnego usunięcia volume według uzgodnionego planu.

## Końcowy protokół GO / STOP

PASS tylko po udokumentowanych Gate1–6: exact checkout(s), device/driver/CUDA,
cgroup/mount/provider/billing, wszystkie model hashes, native receipts, realne
object_info, prompt text/EOS/timeline, H3→10Eros→H3 MP4, VRAM/overlap,
normalne batch/review/priority, recovery, ownership i retencja/lifecycle.
FAIL/NOT RUN dowolnego obowiązkowego punktu pozostaje widoczne. Nie zdejmuj
DavidAU i nie merguj PR w ramach tego audytu. Zmiana źródła/identity/modelu wymaga
nowego review, testów i właściwego powtórzenia zależnych gate'ów.

Wybór i zatwierdzenie hosta można przygotować offline według Gate0 i checklisty
pierwszych 15 minut. Ten acceptance dotyczy pierwszego wdrożenia; normalne
zmiany parametrów generacji i wybór LoRA pozostają dostępne zgodnie z istniejącą
walidacją używanych zasobów oraz frozen job identity. Nie wymagają reinstalacji
całego stacku ani ponawiania całego Gate1–6 dla każdej zmiany formularza.

Aktualnie gotowe są CPU mechanizmy i plan. Brakuje rzeczywistych dowodów GPU,
retirement follow-upu i konkretnej oferty/budżetu/proof
hosta. **Pełne Gate1–6: NO-GO**.
Ograniczony Gate1A można rozpocząć dopiero po osobnym zatwierdzeniu jego
rzeczywistych kosztów i wszystkich warunków wejściowych; nie jest to PASS produkcji.
