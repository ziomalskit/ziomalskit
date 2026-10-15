# Konfiguracja LoRA bez przebudowy środowiska

Nowe zadania H3 Full wybierają domyślnie `hm-breasts-v2` (1.0).
`mysticxxx-v4` i `movement-v1` pozostają w panelu, ale OFF; zachowane strength
to 0.6 i 0.5. 10Eros Full zaczyna z opcjonalnymi LoRA OFF. Conditioning bridge
ma osobne ustawienia profilu; ta zmiana go nie wyłącza.

## Zwykła praca

Checkboxy i strength pierwszego stosu dotyczą tworzonego zadania. Drugi stos
działa wyłącznie przy włączonym second pass. Seed, duration i obsługiwane render
controls również są ustawieniami zadania. Nie wymagają rebuild, provisioningu,
restartu środowiska ani ponownego acceptance wszystkich modeli. Nadal obowiązują
zakresy, zgodność profilu, capacity i admission/memory guards. Pierwsza próba
nowej kombinacji może wymagać lokalnego testu jakości/VRAM; nie stanowi powodu
do ponownego testowania niezmienionych LLM czy lifecycle.

Wyłączony adapter nie wymaga pliku ani enrollment. Włączony adapter przechodzi
przed wykonaniem prawdziwą kontrolę rozmiaru/SHA256 albo jawnego local enrollment.
Zmiana strength nie hashuje ponownie całego stosu: niezmienione bazowe modele
korzystają z istniejących provenance receipts i filesystem signatures. Aktywne
LoRA są nadal hashowane przed submission; cache nie zastępuje ich kontroli.
Nieznany ID, niepoprawny plik, traversal, symlink i niezgodna provenance blokują
wykonanie. Samo utworzenie joba nie jest potwierdzeniem gotowości jego plików.

## Dodanie lub instalacja adaptera

To kontrolowana zmiana katalogu zasobów, nie zmiana kodu ani całego deploymentu.
Nie uruchamiaj ponownie pełnego installera tylko po to, żeby dodać LoRA.

Pełny installer wybiera teraz wymagane artefakty oraz opcjonalne LoRA/bridge
włączone w defaults któregokolwiek profilu. Nie pobiera nieużywanych optional
LoRA ani starego opcjonalnego writera 4B. Jawne `provision_models.py
--include-optional` zachowuje możliwość instalacji całego manifestu; to świadomy
wybór operatora. Manifest 22 źródeł pozostaje niezmieniony. Jego pełny inwentarz
i wcześniejsze wyliczenia transferu są górnym wariantem, a bieżący `--plan`
pokazuje rzeczywiście wybrany zestaw. DavidAU nadal blokuje pełny provisioning.
Wyjątek dla nowej LoRA spoza globalnego manifestu nie dotyczy aktywnego
conditioning bridge: jego przypięta implementacja nadal wymaga zgodnego wpisu
w manifeście. Bridge nie jest dowolnym dodatkowym slotem PowerLoRA.

1. Przygotuj nowy, unikalny ID oraz basename `.safetensors`. W
   `$PANEL_ROOT/config/lora_registry.json` dodaj wpis ze schematem takim jak
   istniejąca LoRA: zakres strength, compatibility i defaults dla **obu** profili.
   Początkowo ustaw OFF w obu profilach, strengths wewnątrz zakresu, GPU pending.
   Nie kopiuj cudzych hashy, rewizji ani nazwy pliku pod istniejący ID.
2. Dla znanego źródła podaj dokładne repo/revision/path/size/SHA256 i
   `verified_source`. Dla własnych bytes użyj `external/user-provided` i null
   source/revision/size/SHA. Nie zgaduj upstream provenance. Publiczny download
   powinien pochodzić z przypiętego źródła; porównaj pełny SHA przed instalacją.
3. Umieść **tylko nowy plik**, bez overwrite, w
   `$COMFY_MODELS_DIR/loras/<filename>`. Nie zmieniaj innych modeli. Dla pliku
   zewnętrznego sprawdź świadomie jego pochodzenie i SHA, następnie wykonaj:

   ```bash
   "$COMFY_PYTHON" "$PANEL_ROOT/scripts/enroll_external_loras.py" \
     --models-root "$COMFY_MODELS_DIR" \
     --registry "$PANEL_ROOT/config/lora_registry.json" my-adapter-id
   ```

   Enrollment działa tylko dla zarejestrowanego ID. Nie nadpisuje wcześniejszego
   enrollment. Zapisuje lokalny SHA, nie poświadcza upstream ani jakości GPU.
4. Sprawdź składnię katalogu bez modeli, sieci i GPU:

   ```bash
   cd "$PANEL_ROOT"
   "$COMFY_PYTHON" - <<'PY'
   from pathlib import Path
   from app.production import read_loras, read_bridge
   path = Path('config/lora_registry.json')
   print(list(read_loras(path)))
   print(read_bridge(path)['id'])
   PY
   ```

5. Kontroler używa jednego niezmiennego snapshotu rejestru do UI, weryfikacji
   oraz kompozycji i walidacji grafu. Nowy wpis wymaga kontrolowanego odświeżenia
   **samego panelu/kontrolera**, bez przebudowy lub ponownego provisioningu modeli.
   Najpierw zakończ bieżące wykonania zwykłym STOP AFTER CURRENT i potwierdź
   trwały zapis kolejki. Dopiero bez aktywnego submit/preparation uruchom:

   ```bash
   bash "$PANEL_ROOT/scripts/service_ctl.sh" restart panel
   ```

   Sprawdź `/api/jobs` i odśwież formularz. Plik dodany pod już istniejący ID
   nie wymaga nowego snapshotu konfiguracji. Przed włączeniem potwierdź jego
   widoczność w realnym `/object_info`; nie restartuj workerów w trakcie pracy.
6. Włącz nowy ID w nowym zadaniu i zacznij od kontrolowanej próby. Zapisz
   zakres zweryfikowanej zgodności, jakość oraz VRAM. Pozostałe modele zachowują
   dotychczasowe dowody; nowa LoRA nie staje się GPU validated przez enrollment.

## Trwałe zadania i granice elastyczności

Zadanie zamraża pełne uporządkowane wybory, strength, bridge i render controls.
Identity wiąże provenance **używanych** LoRA pierwszego i aktywnego drugiego
stosu oraz aktywnego bridge. Nowe wpisy, nowe defaults, zmiany nieużywanych LoRA
i adnotacje prezentacyjne nie zmieniają wykonania zapisanej kolejki. Nieaktywny
wpis może zniknąć z katalogu; zapisane wartości pozostają niezmienione.

Zastąpienie używanego filename/źródła/SHA, zmiana jego funkcjonalnych ograniczeń,
zmiana aktywnego bridge albo implementacji workflow/native nadal wymaga
reconciliation; kontroler nie naprawia takich jobów domysłami. Zmiana enrollment
istniejącego ID również nie jest normalną operacją instalacji — użyj nowego ID
dla innych bytes. Nie usuwaj receipts, żeby zaakceptować podmieniony plik.

Ta poprawka zmienia fingerprint implementacji adaptera. Joby zamrożone przez
starszy kod zachowują dotychczasową bramkę reconciliation; nie można dowieść ich
zgodności na podstawie samego starego globalnego hasha rejestru. Powyższa
zgodność zmian katalogu dotyczy jobów zapisanych przez nową implementację.

Pełna produkcja nadal wymaga DavidAU follow-up po prawdziwym acceptance 27B,
Linux >=6.9 i rzeczywistego PIDFD_SIGNAL_PROCESS_GROUP probe, właściwego GPU,
CUDA, cgroups, storage, admission, ownership i niezduplikowanych submission.
Zmiana LoRA nie jest obejściem tych bramek. CPU nie potwierdza realnej zgodności
LoRA z H3/10Eros. [Runbook GPU](GPU_PRODUCTION_SESSION.md) pozostaje obowiązujący.
