# START HERE — projekt `aj` / H3 / ComfyUI / Vast.ai

**Stan rekonstrukcji: 2026-10-06.**

Ta paczka służy do przeniesienia projektu do nowego czatu ChatGPT albo do Codex bez rekonstruowania stanu z długiej historii rozmowy.

## Co jest aktualnym projektem

Aktualny projekt to system generowania wideo **MiniMax H3 Ref2VA** w ComfyUI, rozwinięty na bazie `Simply Advanced - MiniMax H3`, z własnym wielostopniowym autoprompterem do analizy referencji, dwuprzebiegowym renderem oraz docelowym deploymentem na **Vast.ai / RTX PRO 6000 96 GB**, sterowanym z telefonu (Fold 6) przez własny panel WWW.

Produkcja ma domyślnie używać **native MiniMax H3 INT8**, a 10Eros jest opcjonalnym torem A/B — nie domyślnym zamiennikiem.

## Najważniejsze pliki — kolejność czytania

1. `00_START_HERE/CURRENT_STATE.md` — najważniejszy dokument dla nowego czatu.
2. `00_START_HERE/DECISIONS_AND_HISTORY.md` — dlaczego projekt wygląda właśnie tak.
3. `00_START_HERE/KNOWN_FAILURES_DO_NOT_REPEAT.md` — błędy i ślepe uliczki.
4. `01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5/README.md` — aktualny deployment Vast.
5. `01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5/FULL_PRE_RENTAL_AUDIT.md` — pełny audyt RC5.
6. `01_CURRENT_TRUTH/CURRENT_REF2VA_INT8_CONVROT_JOYCAPTION_v8_3X3_TILES_QWEN_VERIFIER.json` — ostatni główny lokalny workflow native INT8.
7. `01_CURRENT_TRUTH/VAST_H3_MASTER_NATIVE_INT8_96GB.json` — master render workflow przygotowany pod 96 GB.
8. `04_MANIFESTS/VAST_H3_API_MAP.json` — ID wejść/nodów do sterownika.

## Hierarchia „source of truth”

Jeżeli stare pliki są sprzeczne z nowymi, obowiązuje kolejność:

**RC5 deployment > VAST_H3_MASTER_NATIVE_INT8_96GB > CURRENT_REF2VA...v8 > stare checkpointy / logi > legacy Wan2.2.**

Nie należy cofać projektu do wcześniejszego modelu lub architektury tylko dlatego, że stary plik ma nazwę `CURRENT` albo `LAST_GOOD`.

## Ważna uwaga o czacie źródłowym

Nie udało się uzyskać surowego eksportu rozmowy o dokładnym tytule **„Analiza poprzedniego czatu”** jako jednego pliku. Rekonstrukcja została wykonana z: indeksowanego kontekstu poprzednich rozmów, plików projektu `aj`, rzeczywistych workflowów, logów, wcześniejszych handoffów i aktualnego RC5. Z tego powodu paczka zawiera również `SOURCE_LIMITATIONS.md` i manifest odnalezionych plików.
