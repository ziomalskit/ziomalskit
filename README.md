# AJ — panel mobilny i generowanie wideo H3

Projekt ma przyjmować **6 obrazów i opcjonalnie 1 plik audio**, analizować referencje, przygotowywać 10 wariantów promptu i generować wideo przez ComfyUI na GPU Vast.ai. Produkcyjny model pozostaje native MiniMax H3 INT8; 10Eros to opcjonalny wariant porównawczy.

**Stan po ponownej analizie, 6 października 2026:** środowisko do rozwijania panelu działa. Oryginalny RC5 ma potwierdzone błędy instalacji, konwersji workflowów i obsługi kolejki. Nie jest jeszcze gotowy do płatnego renderowania.

Nie musisz ręcznie układać plików na GitHubie. Repozytorium zawiera materiały projektu, powtarzalną instalację, lokalny start panelu i testy. Nie zawiera wag modeli ani prawdziwych haseł.

## Od czego zacząć

1. [Raport działania i kompatybilności](docs/AUDIT_2026-10-06.md) — aktualne ustalenia i kolejność napraw.
2. [Oryginalny README paczki](migration/00_START_HERE/README_FIRST.md) — opis projektu przekazany z wcześniejszej rozmowy.
3. [Oryginalny stan projektu](migration/00_START_HERE/CURRENT_STATE.md) — architektura, modele i historia decyzji.
4. [Przygotowanie do Vast.ai](docs/GPU_ACCEPTANCE.md) — wymagania, których nie można sprawdzić na maszynie bez GPU.

Załączniki są materiałami źródłowymi. Ich wcześniejsze oznaczenia „PASS” odnoszą się do zakresu dawnych testów; obecny audyt wykazał dodatkowe usterki. Paczka nie zawiera pełnego eksportu czatu „Analiza poprzedniego czatu”.

## Praca lokalna / Codex

Wymagane: Linux, Python 3.12, Git i Bash. Node.js jest opcjonalny i służy do sprawdzania składni JavaScript.

```bash
make setup     # osobne .venv, zależności z wersjami i sumami SHA256
make test      # integralność paczki, oryginalne testy, 16 prób HTTP
make panel     # lokalny panel, logowanie i stan w ignorowanym .local/
make status
make stop
```

Panel developerski korzysta z oryginalnego kodu RC5 w kopii `.local/panel/`. Słucha tylko na lokalnym interfejsie, nie instaluje ComfyUI ani modeli i nie ma działającego CLI Vast. Hasło generuje lokalnie w `.local/dev-auth.json`, który jest wykluczony z Git. Procesy trzeba uruchomić ponownie po odtworzeniu środowiska.

```bash
make audit    # odtwarza znane błędy sterownika; obecnie kończy się kodem 1
```

`make test` potwierdza działanie środowiska CPU i kontraktów objętych testami. Nie dowodzi poprawności renderu, pamięci GPU ani automatycznego wyłączania instancji. `make audit` celowo zgłasza istniejące błędy jako blokery, a nie jako zaliczone testy produkcyjne.

## Układ repozytorium

| Katalog | Zawartość |
| --- | --- |
| `migration/` | Wszystkie 66 oryginalnych plików ZIP, zachowane bez zmian |
| `migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5/` | Panel, skrypty, mapowanie API i workflowy RC5 |
| `archive/` | Sześć otrzymanych załączników; dwa dosłane README są identyczne z wcześniejszymi |
| `docs/` | Nowy audyt, dowody i wymagania dla testu GPU |
| `scripts/` | Powtarzalne narzędzia developerskie i testy bez płatnego renderu |
| `requirements/panel.lock` | Dokładne wersje i sumy kontrolne zależności panelu |
| `.github/workflows/` | Automatyczne testy CPU na push i pull request |
| `.local/`, `.venv/` | Lokalny stan, hasła, logi i zależności; poza Git |

Nie uruchamiaj oryginalnego `INSTALL_ON_VAST.sh` na obecnej maszynie Codex. Jest przeznaczony dla serwera GPU, modyfikuje system i wymaga napraw wskazanych w audycie. Zwykły przycisk tworzenia batcha automatycznie zleca pięć renderów; nie jest testem „prompt-only”.
