# Uruchomienie na GPU — czego jeszcze potrzeba

Ten dokument opisuje wymagania, a nie potwierdzoną instalację. W bieżącej maszynie nie ma NVIDIA GPU, CUDA, ComfyUI, wag modeli ani konfiguracji instancji Vast.

## Kolejność przed wynajmem i pierwszym renderem

1. Naprawić blokery instalatora, wersji llama.cpp, konwertera workflowów i kolejki opisane w [audycie](AUDIT_2026-10-06.md). Zachować oryginalną paczkę jako baseline.
2. Ustalić zaufane źródła wag, dokładne rewizje, rozmiary i sumy SHA256. Manifest RC5 zawiera nazwy, ale nie wystarcza do pobrania i zweryfikowania wszystkich modeli. Nie zgadywać URL ani akceptować uszkodzonych plików.
3. Przygotować wybraną instancję RTX PRO 6000 Blackwell 96 GB i trwały wolumen. Potwierdzić rzeczywistą ścieżkę montowania przed instalacją oraz wystarczający dysk/RAM. Nie utożsamiać zwykłego katalogu `/workspace` z niezależnym wolumenem.
4. Wybrać jeden interpreter Python i zgodne wersje sterownika, CUDA oraz PyTorch. Oryginalny skrypt wymusza `cu130`, mimo deklarowanego minimum CUDA 12.8. Sprawdzić rzeczywiste działanie torch CUDA i wymagane architektury GPU.
5. Skonfigurować hasło panelu i dostęp Vast bez publikowania ich w Git lub czacie. Panel Basic Auth udostępniać przez szyfrowaną transmisję. Publicznie wystawiać tylko panel 7860; ComfyUI 8188/8189 pozostawić lokalnie.
6. Zweryfikować instalację dokładnych custom nodes oraz loaderów przez `/object_info`, a także integralność modeli i wybór rzeczywistej binarki LLM. Samo istnienie pliku lub otwarty port nie potwierdza gotowości.
7. Uruchomić poprawione preflight i smoke. Konwersja przez `--print-prompt` nie wysyła zadania. Sprawdzić, że wynikowy graf nie zawiera klas należących wyłącznie do UI, a opcjonalne/bypassowane gałęzie nie są wykonywane.
8. Test prompt-only zlecać osobno, workflowem `VAST_H3_PROMPT_ONLY_STAGE2.json`, bez batcha panelu. To nadal wykonuje obliczenia na wynajętym GPU. Batch panelu automatycznie zleca pięć renderów i nie ma trybu prompt-only.
9. Po poprawnych wynikach wykonać jeden kontrolowany render i potwierdzić rzeczywisty plik wideo oraz status sukcesu w `/history`. Sprawdzić zatwierdzony prompt i unikanie drugiego autopromptera na render-workerze.
10. Dopiero potem zbadać cache Step 0–2, równoległe zużycie VRAM, throughput, odtwarzanie kolejki po restarcie i obsługę lifecycle na konkretnej instancji.

## Sieć i dane dostępowe

Środowisko developerskie CPU potrzebuje działającego HTTPS GitHub oraz PyPI. Do GPU dochodzą źródła PyTorch, Vast i wag modeli, m.in. Hugging Face i jego rzeczywiste domeny transferu. Lista modeli nie ma pełnych adresów, więc nie można jeszcze określić kompletnej listy domen.

W obecnym środowisku nie stwierdzono wymaganych zmiennych H3/Vast/Hugging Face; w konfiguracji nie ma takich bindingów. Nie ma potrzeby dodawania klucza Vast do testów CPU. Wartości wymagane dla realnego wdrożenia należy podać bezpiecznie w ustawieniach środowiska, po ustaleniu konkretnego celu i drogi uwierzytelnienia. Proxy-secret nie jest automatycznie surowym tokenem dla lokalnego pliku lub CLI.

## Czego nie potwierdzono

Nie wykonano instalacji ani inferencji GPU, realnego `/object_info`, testu jakości generowanego filmu, testu cache, benchmarku dwóch procesów, połączenia z instancją Vast, restartu wynajętej maszyny ani operacji STOP/DESTROY. Automatyczne wyłączanie i recovery nie powinny być używane do pilnowania kosztów przed naprawą potwierdzonych błędów.
