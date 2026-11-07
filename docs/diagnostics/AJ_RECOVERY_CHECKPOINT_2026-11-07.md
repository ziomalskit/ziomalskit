# AJ H3 + 10Eros — punkt wznowienia po sesji 7 listopada 2026

## Instancja i repozytorium

- Repo: `ziomalskit/ziomalskit`
- Zapisana gałąź: `diag/vast-init-59453490`
- Vast.ai istniejąca instancja: `59453490` — **nie wynajmować nowej bez zgody użytkownika**.
- Obraz uruchomieniowy nie był przebudowany ani zaktualizowany tą sesją. Zmiany kontrolera i modułów ComfyUI są lokalnymi zmianami w działającym kontenerze, a źródła napraw zapisano na gałęzi diagnostycznej.
- W repozytorium działającego kontenera nie ma metadanych `.git`; zwykły `git pull` w `/workspace/H3_VAST_MOBILE` nie odtworzy środowiska.

## Dokładne potwierdzone kopie kodu (SHA-256)

| Plik w repo na gałęzi | SHA-256 | Miejsce instalacji na Vast |
|---|---|---|
| `h3/app/main.py` | `8656d1e4eaf001cd4411a87f51c046cacb63d4acf271577ca7cc729504aefbfc` | `/opt/aj/ziomalskit/h3/app/main.py` i `/workspace/H3_VAST_MOBILE/app/main.py` |
| `h3/custom_nodes/aj_production/prompt_completion.py` | `b189c06b70bde2444ab5484757503f7e33df5feac3efdf994c7d97434aef68cf` | `/workspace/H3_VAST_MOBILE/custom_nodes/aj_production/prompt_completion.py` i `/workspace/ComfyUI/custom_nodes/aj_production/prompt_completion.py` |
| `h3/custom_nodes/aj_production/temporal.py` | `df7bffa15448fdac2a31db01e25f5bc884f9957a3c32b5ccbbecd697abb06e99` | `/workspace/H3_VAST_MOBILE/custom_nodes/aj_production/temporal.py` i `/workspace/ComfyUI/custom_nodes/aj_production/temporal.py` |

Wszystkie trzy sumy plików na gałęzi były porównane z sumami działającego kodu i potwierdzone po zatwierdzeniu najnowszego kontrolera w commicie `a97e1ff1a1cebda973d6038f248ab8469070fd43`.

## Funkcjonalność / poprawki zapisane w powyższym kodzie

- Mistral Step 1/2: rozszerzenie kontekstu i budżetu wyjścia; zwięzłe instrukcje analizy.
- Qwen3.5-27B Step 3/4: offload wszystkich 64 warstw na RTX PRO 6000 (limit `n_gpu_layers=100`).
- Poprawka `cached_text`: nie wymuszaj nieistniejących po pruning selektorów Step 1/2; zachowaj selektory Step 3/4.
- Normalizacja odwołań `[Shot 1]` w środku zdań bez wyłączenia walidacji czasu.
- Automatyczne zwalnianie VRAM renderera po ukończonym MP4.
- Wyniki i pomiary: `docs/diagnostics/PERFORMANCE_AUDIT_2026-11-07.md`.

## Niezabezpieczona część (ważne)

Odczyt `findmnt -T` z istniejącej instancji pokazał dla **wszystkich** poniższych katalogów `/ overlay overlay`, bez odrębnego zamontowanego wolumenu trwałego:

- `/workspace/ComfyUI/models` (pobrane wagi modeli)
- `/workspace/ComfyUI/output` (wyniki, MP4)
- `/workspace/H3_VAST_MOBILE/state` (kolejka i analiza)
- `/workspace/.aj-diagnostics` (kopie lokalne, w tym poprzednie pliki przed poprawkami)

**Nie usuwać/destroy instancji** — to może skasować jedyny egzemplarz tych danych. Samo `Stop` i późniejszy `Start` tej samej instancji zachowuje jej dysk według dokumentacji Vast, ale naliczana jest opłata za storage; nie jest to kopia zapasowa na niezależnej infrastrukturze. Sprawdź zakończenie aktywnego MP4 przed zatrzymaniem.

Nie przeniesiono tu żadnych modeli, filmów, plików wejściowych ani stanu kolejki na osobne przechowywanie poza Vast. Nie umieszczać w repozytorium prywatnych danych wejściowych, sekretów ani wygenerowanych plików.

W migawce diagnostycznej kolejka zawierała 150 zadań: 139 historycznie `prompt_failed`, 4 `completed`, 1 `render_running`, 1 `render_queued_auto`, 5 `prompt_queued`. Te liczby zmieniają się, ponieważ instancja wykonuje pracę.

## Dostęp i cykl życia

- Działał pod `/workspace/.aj-diagnostics` lokalny proces `aj_gh_key_rotator.py`: dodaje wyłącznie ograniczony klucz GitHub Actions do `/root/.ssh/authorized_keys`; poprzednie klucze są zachowane. Kod odtwarzający zapisano w `docs/diagnostics/aj_gh_ssh_guard.py`, `aj_gh_key_rotator.py`, `install_gh_ssh_rotator.py`.
- Aktualizacja klucza ma postać procesu w działającym kontenerze; **automatyczne uruchamianie po Stop/Start nie jest potwierdzone**.
- Tymczasowy runner diagnostyczny: `.github/workflows/aj-live-init-59453490.yml`, control `docs/diagnostics/59453490-control.json`, status `docs/diagnostics/59453490-status.json`. Runner ma skończony czas życia. Nie traktować go jako kopii danych.
- Nie zmieniono domyślnie jakości H3: nadal 481 klatek / 24 FPS, 24+4 kroków; profil Fast był jedynie rekomendowany, nie wdrożony.
- Brak potwierdzenia, że pełny stack jest już pozbawiony błędów.

## Najbezpieczniejsze wznowienie

1. Przeczytać niniejszy checkpoint i `PERFORMANCE_AUDIT_2026-11-07.md`.
2. Jeśli instancja nadal istnieje, sprawdzić jej status i czas życia hosta; **nie wynajmować kolejnej instancji**.
3. W razie odtworzenia kontenera z obrazu, wgrać powyższe pliki z gałęzi diagnostycznej w oba wskazane miejsca i sprawdzić SHA-256, uprawnienia oraz health check.
4. Zabezpieczyć / wykopiować modele, outputy i `state` na niezależny storage **przed** usunięciem instancji.
5. Następnie przetestować kolejkę i status już rozpoczętych renderów, bez ponownego masowego generowania i bez marnowania środków na powtórne instalowanie wag.
