# SOURCE LIMITATIONS / kompletność

## Co udało się odzyskać

- aktualne główne workflowy native/10Eros;
- finalny RC5 deployment wraz z pełnym rozpakowanym systemem;
- API map, audit i SHA256;
- kilka oryginalnych H3 workflowów wejściowych i główną bazę `simplyAdvancedMinimax_v154.json`;
- rzeczywiste logi błędów z 3 i 6 października;
- historyczny handoff Wan2.2/Stand-In oraz kluczowe screenshoty;
- z indeksowanego kontekstu rozmów: kolejność decyzji, rollbacków, modeli i przejście na Vast.

## Czego nie można było skopiować 1:1

Część plików zapisana bezpośrednio jako **Project files** w `aj` jest czytelna przez system wyszukiwania/odczytu, ale nie udostępnia autoryzowanej ścieżki do materializacji surowych bajtów. Takie pliki są wyszczególnione w `DISCOVERED_FILES_MANIFEST.csv` jako `raw_bytes_unavailable_from_project_surface` i ich istotna wiedza została przeniesiona do dokumentów handoff.

## Świadomie niewłączone ciężkie modele

Waga `scanner_low_convert(1).safetensors` (~306.8 MB) należała do historycznej gałęzi Wan2.2 Scanner LOW i nie jest potrzebna do kontynuacji aktualnego H3/Vast. Nie kopiowano również wielogigabajtowych wag H3/LLM — paczka zawiera ich dokładne nazwy i manifest deploymentu, nie same modele.

## Rozmowa „Analiza poprzedniego czatu”

System nie udostępnił surowego eksportu całej rozmowy pod tym dokładnym tytułem. Dlatego handoff opiera się na materialnych artefaktach projektu i odzyskanym kontekście rozmów. To jest istotne ograniczenie i nie należy twierdzić, że ZIP jest literalnym eksportem każdej wiadomości z tego czatu.
