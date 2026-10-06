# HANDOFF — ComfyUI / Wan2.2 I2V / chainowanie segmentów / Stand-In identity

## SCREENY, KTÓRE DOŁĄCZAM DO TEGO CZATU

**1.** **`Screenshot 2026-09-10 222202.png`**

Mój dotychczasowy/native Wan2.2 workflow. Widać m.in.:

-  dwa `ModelSamplingSD3` 
-  dwa `KSampler (Advanced)` — HIGH i LOW 
- `Switch (Model)`, `Switch (Steps)`, `Switch (CFG)`, `Switch (Split Step)` 
-  moje dodatkowe `Load LoRA` 
-  LOW branch ze `scanner_low_convert.safetensors` 
-  HIGH/LOW R1 widoczne na screenie jako osobne elementy 

**2.** **`Screenshot 2026-09-11 004721.png`**

Wyniki wyszukiwania `Stand-In` po poprawnej instalacji. Widoczne są:

- `Stand-In Processor Loader` 
- `Stand-In Trimmer & Cropper` 
- `Stand-In Background Restorer` 
- `Stand-In VideoInputPreprocessor` 
- `Apply Stand-In Processor` 
- `WanVideo Add StandIn Latent` 
- `Face Only Mode Switch (Stand-In)` 

**3.** **`Screenshot 2026-09-11 010234.png`**

Aktualnie otwarty przykładowy workflow **Stand-In + WanVideoWrapper**. To jest teraz nasz punkt wyjścia do przebudowy na Wan2.2 I2V HIGH/LOW.

---

# 1. MÓJ CEL

Buduję w ComfyUI automatyczny workflow video składający się z kilku kolejnych segmentów, np.:

```
```

```
Segment 1 (5 s)
↓
wybrana końcowa / dobra klatka
↓
Segment 2 (5 s, osobny prompt)
↓
wybrana klatka
↓
Segment 3...
↓
merge klatek
↓
jeden finalny MP4
```

Każdy segment może mieć **osobny prompt**.

Segment 2 powinien:

-  zaczynać się od dobrej klatki z końca segmentu 1, 
-  ale jednocześnie zachować **identity / twarz z ORYGINALNEGO PIERWSZEGO ZDJĘCIA**, a nie polegać wyłącznie na twarzy z wygenerowanej klatki. 

Docelowa logika:

```
```

```
ORYGINALNE PIERWSZE ZDJĘCIE
         ↓
  STAŁY IDENTITY REFERENCE
         ↓
      Stand-In
         ↓
         ┐
         │
DOBRA KLATKA Z SEGMENTU 1
         ↓
      SEGMENT 2
```

Czyli:

- **last good frame = continuity** 
- **first/original image = identity** 

To jest najważniejszy cel całego projektu.

---

# 2. DOTYCHCZASOWY NATIVE WAN2.2 WORKFLOW

Korzystałem głównie z oficjalnego:

```
```

```
Workflow → Browse Templates → Video → Wan2.2 14B I2V
```

Subgraph `Image to Video (Wan2.2)` ma:

-  width / height 
-  duration 
-  noise\_seed 
-  HIGH model 
-  HIGH Lightning LoRA 
-  LOW model 
-  LOW Lightning LoRA 
-  CLIP 
-  VAE 
-  Turbo switch 

Bazowe modele używane wcześniej:

```
```

```
wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
```

Text encoder:

```
```

```
umt5_xxl_fp8_e4m3fn_scaled.safetensors
```

VAE:

```
```

```
wan_2.1_vae.safetensors
```

Lightning:

```
```

```
wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors
wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors
```

Standard/native:

```
```

```
steps = 20
CFG = 3.5
split_step = 10
shift = 5
```

Turbo:

```
```

```
steps = 4
CFG = 1.0
split_step = 2
```

HIGH i LOW oznaczają **etapy odszumiania**, a NIE początek/koniec czasu filmu.

---

# 3. MOJE DODATKOWE LORA / MODELE

## Scanner

Mam:

```
```

```
scanner_low_convert.safetensors
```

To jest Scaner ND LOW dla Wan2.2 I2V LOW NOISE.

Poprawne miejsce w native workflow było:

```
```

```
LOW Switch(Model)
↓
scanner_low_convert
↓
LOW ModelSamplingSD3
↓
LOW KSampler
```

Scanner był zwykle testowany przy:

```
```

```
strength_model = 1.0
```

Do przyszłych testów warto sprawdzić również 0.5–0.7, bo przy dwóch postaciach może zbyt mocno narzucać kompozycję.

## R1

Testowałem również:

```
```

```
Wan2.2_I2V_High_R1.safetensors
Wan2.2_I2V_Low_R1.safetensors
```

Są to pełne modele/finetune HIGH/LOW, a nie zwykłe małe LoRA.

Na screenie `2026-09-10 222202` są widoczne w `Load LoRA`; ten układ był zakwestionowany i **nie należy zakładać, że jest poprawny**. W nowym workflow trzeba to zweryfikować zamiast kopiować 1:1.

## Inne LoRA

Mam/miałem dodatkowe HIGH/LOW LoRA do:

-  anatomii / transformacji 
-  kissing / interaction 
-  Scanner 

Ważne:

-  HIGH LoRA powinno iść do HIGH branch 
-  LOW LoRA do LOW branch 

Nie chcę teraz od nowa omawiać wszystkich LoRA — najpierw odtworzyć stabilny Wan2.2 + Stand-In.

---

# 4. CHAINOWANIE SEGMENTÓW JUŻ DZIAŁAŁO

Udało nam się zrobić:

```
```

```
Wan segment #1
↓
decoded IMAGE batch
↓
wybór klatki
↓
Wan segment #2 input image
```

Początkowo było to `LAST_FRAME`.

Później wyszło, że literalnie ostatnia klatka nie zawsze jest dobrym anchor frame, np. jeśli:

-  twarz patrzy w dół, 
-  twarz jest zasłonięta, 
-  poza jest przejściowa, 
-  model zniekształcił twarz. 

Dlatego docelowo chcę:

```
```

```
LAST_GOOD_FRAME
```

np. N-3 / N-5 / N-8 zamiast zawsze ostatniej.

Przy 81 klatkach można testować np.:

```
```

```
80 = ostatnia
76
74
72
```

Segment 2 już potrafił automatycznie wystartować z wybranej klatki segmentu 1.

---

# 5. MERGE SEGMENTÓW

Ustaliliśmy, że najlepsza architektura to łączyć **IMAGE batches przed Save Video**, nie gotowe MP4:

```
```

```
frames segment 1 ─┐
                  ├→ IMAGE batch merge
frames segment 2 ─┘
                  ↓
              Save Video
                  ↓
                MP4
```

Możliwa jest jedna zdublowana klatka na seamie:

```
```

```
ostatnia #1
pierwsza #2
```

bo #2 startuje od frame'a wybranego z #1.

Później można wyciąć pierwszą klatkę segmentu 2.

---

# 6. DLACZEGO W OGÓLE POTRZEBUJĘ STAND-IN

Sam Wan2.2 I2V dobrze trzyma twarz z **pierwszego zdjęcia w pierwszym segmencie**.

Problem pojawia się przy:

```
```

```
segment 1 → wygenerowana klatka → segment 2
```

Segment 2 widzi jako input już wygenerowaną klatkę, więc jeśli:

-  twarz patrzy w dół, 
-  jest pod kątem, 
-  jest częściowo niewidoczna, 
-  uległa małemu driftowi, 

kolejny segment może wygenerować identity gorzej.

Chcę więc przekazywać **oryginalne pierwsze zdjęcie cały czas jako osobny identity reference**.

Sam prompt typu:

```
```

```
preserve same face / identity
```

nie wystarcza.

Dlatego wybraliśmy **Stand-In**.

---

# 7. STAND-IN — CO JUŻ ZROBIONO

Zainstalowany jest:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI\custom_nodes\Stand-In_Preprocessor_ComfyUI
```

Repo:

```
```

```
WeChatCV/Stand-In_Preprocessor_ComfyUI
```

WanVideoWrapper również mam zainstalowany.

Po restarcie widzę:

```
```

```
Stand-In Processor Loader
Stand-In Trimmer & Cropper
Stand-In Background Restorer
Stand-In VideoInputPreprocessor
Apply Stand-In Processor
Face Only Mode Switch (Stand-In)
WanVideo Add StandIn Latent
```

Patrz screenshot:

```
```

```
Screenshot 2026-09-11 004721.png
```

---

# 8. WAŻNA HISTORIA AWARII .VENV

Przy pierwszej próbie instalacji dependencies Stand-In:

```
```

```
pip install ...
```

instalacja `onnxruntime-gpu` zakończyła się:

```
```

```
WinError 5 Access is denied
onnxruntime_providers_shared.dll
```

ponieważ ComfyUI prawdopodobnie trzymało DLL.

W tym samym czasie:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI\.venv\Scripts\python.exe
```

został wyzerowany do:

```
```

```
0 bytes
```

i ComfyUI zaczął dawać:

```
```

```
operation failed spawn EFTYPE
```

Naprawiliśmy to.

`pyvenv.cfg`:

```
```

```
home = A:\ComfyUI\ComfyUI\standalone-env
implementation = CPython
uv = 0.12.9
version_info = 3.13.12
include-system-site-packages = false
```

Bazowy Python:

```
```

```
A:\ComfyUI\ComfyUI\standalone-env\python.exe
Python 3.13.12
```

Ostateczna poprawna naprawa:

```
```

```
ren "A:\ComfyUI\ComfyUI\ComfyUI\.venv\Scripts\python.exe" "python.exe.BAD"
ren "A:\ComfyUI\ComfyUI\ComfyUI\.venv\Scripts\pythonw.exe" "pythonw.exe.BAD"

"A:\ComfyUI\ComfyUI\standalone-env\uv.exe" venv --allow-existing --python "A:\ComfyUI\ComfyUI\standalone-env\python.exe" "A:\ComfyUI\ComfyUI\ComfyUI\.venv"
```

Po naprawie:

```
```

```
"A:\ComfyUI\ComfyUI\ComfyUI\.venv\Scripts\python.exe" --version
```

zwraca:

```
```

```
Python 3.13.12
```

oraz:

```
```

```
"...\.venv\Scripts\python.exe" -c "import sys; print(sys.prefix)"
```

zwraca:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI\.venv
```

**Nie wolno ponownie kopiować** **`standalone-env\python.exe`** **ręcznie do** **`.venv\Scripts\python.exe`****.**

Poprawna metoda naprawcza to `uv venv --allow-existing`.

---

# 9. ZAINSTALOWANE DEPENDENCIES STAND-IN

`pip show` potwierdził:

```
```

```
mediapipe        0.10.30
onnxruntime-gpu  1.30.0
ultralytics      8.4.146
facexlib         0.3.0
huggingface_hub  1.30.0
```

Wszystkie są w:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI\.venv\Lib\site-packages
```

Test:

```
```

```
"A:\ComfyUI\ComfyUI\ComfyUI\.venv\Scripts\python.exe" -c "from ultralytics import YOLO; from facexlib.parsing import init_parsing_model; import onnxruntime; import mediapipe; print('WSZYSTKO OK')"
```

przeszedł:

```
```

```
WSZYSTKO OK
```

Czyli **nie instalować tych dependencies ponownie bez powodu**.

---

# 10. AKTUALNY STAN WANVIDEOWRAPPER + STAND-IN

Patrz:

```
```

```
Screenshot 2026-09-11 010234.png
```

Jest tam przykładowy workflow Stand-In.

Na screenie widać m.in.:

```
```

```
Load Image
↓
Apply Stand-In Processor
↑
Stand-In Processor Loader
```

Dalej widzę:

```
```

```
WanVideo Encode
WanVideo Add StandIn Latent
WanVideo Empty Embeds
WanVideo TextEncode
WanVideo Model Loader
WanVideo Block Swap
WanVideo Set LoRAs
WanVideo Sampler
WanVideo Decode
Image Concatenate Multi
Video Combine
```

Są również dwa `WanVideo Lora Select`.

Jeden czerwono zaznaczony po lewej ma Stand-In LoRA/reference.

Drugi u góry ma LightX2V.

Ten workflow jest obecnie tylko **przykładem / szkieletem**, nie moim finalnym workflow.

Wygląda na to, że przykład jest ustawiony pod **Wan2.1/T2V**, więc NIE klikamy po prostu Queue i NIE zakładamy, że można go użyć bez zmian dla Wan2.2 I2V.

---

# 11. CO CHCĘ ODTWORZYĆ W WANVIDEOWRAPPER

Chcę możliwie wiernie odtworzyć mój wcześniejszy native tor:

```
```

```
Wan2.2 I2V A14B
HIGH expert
LOW expert
↓
osobne HIGH/LOW LoRA
↓
Scanner LOW
↓
ewentualne inne interaction/anatomy LoRA
↓
I2V
↓
decoded frames
```

i dodać:

```
```

```
ORIGINAL FIRST IMAGE
↓
Stand-In identity
```

do **każdego kolejnego segmentu**.

Segment 2 powinien dostać jednocześnie:

```
```

```
input/continuity:
selected frame from segment 1

identity:
original first image
```

---

# 12. WAŻNE: MAM RTX 5080 16 GB

GPU:

```
```

```
NVIDIA RTX 5080
~16 GB VRAM
```

Dlatego preferuję:

-  FP8 / quantized modele zamiast BF16 
-  sensowny block swapping 
-  LightX2V/Turbo, jeśli nie rozwala jakości 

Native FP8 Wan2.2 działał u mnie.

---

# 13. INSTALACJA COMFYUI / ŚCIEŻKI

Główna instalacja:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI
```

Python venv:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI\.venv\Scripts\python.exe
```

Standalone Python:

```
```

```
A:\ComfyUI\ComfyUI\standalone-env\python.exe
```

Custom nodes:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI\custom_nodes
```

Stand-In preprocessor:

```
```

```
A:\ComfyUI\ComfyUI\ComfyUI\custom_nodes\Stand-In_Preprocessor_ComfyUI
```

Mam również ścieżki Comfy Desktop shared models, więc przy modelach trzeba uważać, **który folder faktycznie skanuje dany node**.

---

# 14. CO MAM ZROBIĆ TERAZ — NAJWAŻNIEJSZE

Nie chcę kolejnej ogólnej teorii.

Na podstawie załączonego:

```
```

```
Screenshot 2026-09-11 010234.png
```

chcę teraz krok po kroku:

**A.** ustalić, które elementy tego przykładowego workflow Stand-In zostawiamy,

**B.** zamienić jego Wan2.1/T2V setup na **Wan2.2 I2V A14B HIGH + LOW**,

**C.** ustawić poprawne FP8 HIGH/LOW pod RTX 5080 16 GB,

**D.** podłączyć Stand-In identity z oryginalnego pierwszego zdjęcia,

**E.** później wprowadzić do tego mój `LAST_GOOD_FRAME` jako I2V continuity dla segmentu 2,

**F.** na końcu przenieść Scanner i moje LoRA HIGH/LOW.

Nie chcę przebudowywać wszystkiego naraz. Robimy po jednym kroku i sprawdzamy.

---

# 15. WAŻNE ZASADY ODPOWIEDZI

Jestem początkujący w CMD/PowerShell/Python.

Jeśli potrzebna jest komenda, podaj mi **dokładną linię do skopiowania**, np.:

```
```

```
"A:\...\python.exe" -m ...
```

Nie pisz:

```
```

```
„zrób to w Pythonie”
„uruchom pip”
„wrzuć gdzieś do models”
```

bez dokładnej ścieżki i polecenia.

W GUI ComfyUI też chcę:

```
```

```
NODE A output X
→ NODE B input Y
```

zamiast ogólnych opisów.

Jeśli screen nie pokazuje potrzebnego wejścia/wyjścia, poproś o zbliżenie zamiast zgadywać.

**Nie instaluj ponownie dependencies Stand-In — są już zainstalowane i przetestowane.**

**Nie każ mi usuwać** **`.venv`****.**

**Nie kopiuj ręcznie** **`standalone-env\python.exe`** **do** **`.venv\Scripts`****.**

---

# 16. PUNKT STARTOWY NOWEGO CZATU

Zacznijmy od:

**„Przeanalizuj dokładnie** **`Screenshot 2026-09-11 010234.png`** **i powiedz, jak z tego przykładu zrobić minimalny działający Wan2.2 I2V HIGH/LOW + Stand-In. Na razie bez chainowania dwóch segmentów i bez dodatkowych LoRA. Najpierw jeden segment Wan2.2 I2V + identity reference.”**