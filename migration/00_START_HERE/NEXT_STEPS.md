# NEXT STEPS — gdzie projekt faktycznie skończył

## Najbliższy krok

Nie przebudowywać kolejny raz workflowu lokalnego. Najbliższy sensowny krok to **live acceptance na Vast.ai** na wybranej instancji RTX PRO 6000 96 GB według RC5.

1. Utworzyć/zweryfikować persistent volume i poprawne mount path.
2. Wystartować właściwy obraz Vast z CUDA zgodnym z provisioningiem.
3. Ustawić `.env` / credentials bez wystawiania sekretów do frontendu.
4. `bash INSTALL_ON_VAST.sh`.
5. `bash scripts/preflight.sh` — musi PASS.
6. `bash scripts/smoke_test.sh` — musi PASS i nie może wykonać płatnego H3 renderu.
7. Test prompt-only: 6 małych obrazów, bez audio.
8. Jeden kontrolowany native INT8 render.
9. Sprawdzić, czy candidate #2+ wykorzystuje cache Step 0–2.
10. Benchmark jednoczesnego prompt-worker + render-worker i sprawdzić VRAM/throughput.
11. Dopiero po tym uruchamiać pełny batch 10 kandydatów i testować lifecycle STOP/START/DESTROY.

## Czego na razie nie robić

- nie zmieniać production defaultu na 10Eros bez A/B;
- nie wracać do NVFP4/Mixed core swap tylko dla oszczędności VRAM;
- nie dodawać z powrotem 16GB unload hacks do mastera 96GB;
- nie rozszerzać pierwszego deployu ponad 6 images + max 1 audio, zanim podstawowy contract nie przejdzie live acceptance;
- nie wystawiać 8188/8189 publicznie; publiczny ma być panel 7860;
- nie renderować „na próbę” przed preflight/smoke, bo właśnie temu służy RC5.
