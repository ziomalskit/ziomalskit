# Opcjonalna diagnostyka GPU — pełny Gate 1–6

**Aktualna codzienna ścieżka instalacji i pierwszych dwóch renderów:**
[Personal First Run](PERSONAL_FIRST_RUN.md). Ten dokument zachowuje szersze
próby regresyjne: prompt-only obu profili, H3 → 10Eros → H3, render controls,
overlap, recovery i live lifecycle. Nie jest obowiązkową certyfikacją pierwszej
sesji, każdego LoRA ani zmiany parametrów. Nie zakładaj, że cały zestaw zmieści
się w $15/4h; osobno zaplanuj i zatwierdź dodatkowe koszty.

## Gate 0–2 — przygotowanie, modele, infrastruktura

Wykonaj preflight, provisioning i smoke według Personal First Run. Linux z działającymi zwykłymi pidfd i /proc
**oraz rzeczywisty probe `process_identity.py support`**, RTX PRO 6000 Blackwell
96 GB, CUDA13.0, zgodny driver, efektywne cgroup/RAM/CPU, pełne source/revision/
size/SHA i prywatny dostęp pozostają wymagane. Każdy nieznany fakt oznacza STOP.
Wagi są jednym rzeczywistym store `COMFY_ROOT/models`; oddzielne są dowody i
małe runtime'y acceptance. Nie kopiuj GGUF/receiptów i nie twórz symlinków.
DavidAU 9B jest usunięty z aktywnego manifestu: nie ma retirement ani przyszłego
cutover SHA. Oba etapy writera 10Eros mają Qwen3.5-27B Q5_K_M, GPU pending.

Rachunek bieżącego instalatora: wymagane17=277909390584B; HM77580016B +
Bunny22045536B → domyślne19=**278009016136B**. Cały jawny bundle22=
284661829208B. Podzbiór11GGUF=113395969792B; pozostały transfer164613046344B.
Największy plik66280487368B jest już częścią wag, nie drugą kopią stagingu.
Cold model-only peak i final to278009016136B; pozostały free po poprawnym reuse11
musi pomieścić164613046344B plus środowiska/build/cache/wyniki/rezerwy.
Szczegółowy final/peak całego systemu, założenia320–350GB oraz kalkulator cen/
transferu/retry są w Personal First Run. Poprzedni rachunek dwóch store i
457685473296B nie opisuje obecnej ścieżki. Instalator nie pobiera opcjonalnych
OFF; `--include-optional` to świadomy pełny bundle. Własne LoRA według
[LORA_CONFIGURATION.md](LORA_CONFIGURATION.md), weryfikacja używanych zasobów.

Przy pełnej diagnostyce rozszerz native probes o [Phase1 C](GPU_ACCEPTANCE_PHASE1.md),
uwzględniając aktualny **wspólny** `AJ_MODELS=$COMFY_MODELS_DIR/LLM`. Nie twórz
jego drugiego fizycznego odpowiednika. Zachowaj Step3OFF, Step4ON, reasoning/
final/natural EOS/no truncation, six-reference context i timeline20s→481→20.040s.
Minimalny CLI exit0 nie dowodzi tych właściwości. Nie zmieniaj GPU pending bez
rzeczywistych wyników konkretnych modeli/ścieżek.

Przed poniższymi komendami zapisz prywatny nowy katalog dowodów i absolutne
ścieżki sześciu rzeczywistych refs, audio i user-intent. `AJ_CURL_CONFIG` to
prywatny plik curl mode600 zawierający Basic Auth, tworzony przez getpass w
Personal First Run. Żadnych tokenów/prywatnych wejść w publicznym PR.

```bash
export AJ_SESSION_DIR=/workspace/private/full-diagnostics
mkdir -m 700 "$AJ_SESSION_DIR"
export AJ_CURL_CONFIG=/workspace/private/curl.conf
export PICTURE_DIR=/workspace/private/real-six-refs
export AJ_AUDIO=/workspace/private/reference.wav
export AJ_SCENE_FILE=/workspace/private/user-intent.txt
export AJ_REFERENCE_1="$PICTURE_DIR/1.png" AJ_REFERENCE_2="$PICTURE_DIR/2.png"
export AJ_REFERENCE_3="$PICTURE_DIR/3.png" AJ_REFERENCE_4="$PICTURE_DIR/4.png"
export AJ_REFERENCE_5="$PICTURE_DIR/5.png" AJ_REFERENCE_6="$PICTURE_DIR/6.png"
```

Nie twórz tych plików z testowych fixtures. Przed startem usług zewnętrzny
ledger odnotowuje actual billing od początku wynajmu; każdy etap i cała sesja
ma deadline oraz rezerwę cleanup. Panelowy cost guard obejmuje estymatę compute
epoki kontrolera, nie wszystkie opłaty/transfer/całą sesję. Pozostaje dodatkową
blokadą; nie zastępuje providera, szczególnie po restarcie panelu.

Local Volume jest opcjonalny. Próby Gate6 wymagające retencji wykonuj **tylko**
z faktycznym verified retained volume i osobno zatwierdzonym kosztem. Ephemeral
nie może nadać keep-data destroy; pozostałe niewykonane próby oznacz NOT RUN.

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
uruchamia startup/controller workers, nie kontaktuje Render POST. Każdy wymagany pending/source mismatch nadal blokuje eksport; nie ma allow_pending.

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
| `destroy_after_queue_keep_data` | Tylko verified retainedvolume i backup; queue drained, destroy compute nievolume. Nowy compute na **tej samej machine**, ten samLocalVolume, reverifyattachment i nowy proof, onstart/bootstrap zatwierdzonego checkoutu bez model redownloadu i bez duplikacji. To kolejny billing/instanceID w limicie sesji. |
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

## Końcowy zapis wyników

Każdy rzeczywiście wykonany gate ma własny PASS/FAIL i dowody, niewykonany ma
NOT RUN. Full Gate1–6 PASS wymaga wszystkich zadeklarowanych prób, także
lifecycle/retencji, jeśli taki zakres wybrano. Wyniku ograniczonego Personal
First Run nie przedstawiaj jako pełnej akceptacji overlap/recovery/lifecycle.
Nie merguj PR ani nie osłabiaj procesów/queue/provenance dla uzyskania PASS.
Normalne zmiany seed/duration/LoRA nie wymagają reinstalacji lub pełnej sesji.

Na obecnym etapie brakuje realnych dowodów GPU i konkretnej zatwierdzonej oferty.
To nie jest blokada instalacji zweryfikowanych wag ani nowy proces retirement.
