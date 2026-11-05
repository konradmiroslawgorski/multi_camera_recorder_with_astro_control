# POE1 (sikorki): instrukcja startowa dla nowego agenta

## Jak zacząć (kolejność)

1. **Zdefiniuj cel jednym zdaniem.** Co ma wykryć kamera POE1 (obiekt, zdarzenie, pora dnia) i co ma się stać po wykryciu: klip, powiadomienie ntfy, tylko CSV?
: Jest budka, mniej więcej prostokątna , do znalezienia i trzeba wykrywać małe ptaki które na niej siadają, na powierzchni "prostokąta" ale też i na jego krawędziach. 
Przykładowe obrazki dostaniesz... 
Wynikiem ma być klip od wykrycia ptaszka, długi 1 minuta, csv niech będzie
2. **Zbierz 5–10 próbek.** Pliki z wykrytym zdarzeniem, bez niego i trudne (noc, deszcz, zmiana oświetlenia). Każdy z krótkim opisem, co dzieje się w której sekundzie. To najważniejszy krok, bo bez prawdy odniesienia nie da się stroić ani sprawdzić fałszywych alarmów.
Przykładowe obrazki dostaniesz... pytanie czy musza to być klatki filmu czy wystarczy pełny printscreen - dużo łatwiej dla mnie!
3. **Zbadaj kadr przed pisaniem kodu.** Ruch kamery, rozmiar obiektu, oświetlenie, stałe obszary. Z tego ma wynikać metoda (różnica tła, detekcja ruchu, model), a nie z założeń z POE_BRAMA.
Kamera może się przesunąć, mogę zadbać by była mniej więcej po środku kadru ale powinna być jakaś korekcja gdyz ptaki na niej siadają i ciut przesuwają, minimalnie ale jednak
4. **Zacznij od najprostszego wariantu**, zmierz go na próbkach i dopiero potem komplikuj.
   - Pierwszy eksperyment porównuje jasność w kolejnych klatkach tylko w ROI
     budki, dla opóźnień 1, 3 i 5 klatek (40, 120 i 200 ms). Mierzy liczbę
     zmienionych pikseli i największy spójny obszar. To pomiar ruchu, nie
     gotowy klasyfikator ptak/owad.
   - Pierwszy pomiar używał dużego, prostokątnego ROI `(800, 400, 300, 500)`.
     Wykazał, że przeloty na tle drzew mogą trafiać do listy, więc obecnie
     detektor maskuje obraz wielokątem dachu z czerwonego obrysu na zrzucie.
     Wierzchołki dla 1920×1080: `(830, 481)`, `(986, 440)`,
     `(1082, 853)`, `(947, 909)`. Obszar poza wielokątem nie bierze udziału
     w pomiarze.
     Ponowna analiza `13-22-28_25_25.mkv` z maską usunęła kandydata
     48,76–49,00 s obecnego w prostokątnym ROI (prawdopodobny ruch poza
     dachem); pozostało 17 przedziałów dla trzech opóźnień, w tym kilka
     wymagających obejrzenia i oznaczenia.
   - Eksperymentalny pomiar `mcrwac/analysis/poe1_bird_motion.py` zapisuje
     szczegółowy CSV dla każdej klatki oraz plik `_events.csv` z przedziałami
     zmian, których największy spójny obszar przekracza domyślne 150 px.
     To kandydaci do obejrzenia, nie wykrycia ptaka; próg można zmienić przez
     `--min-component-px`. Przykład:
     ```bash
     ./venv_motion/bin/python -m mcrwac.analysis.poe1_bird_motion \
       /opt/cameras/recordings/POE1/2026-10-27/13-22-28_25_25.mkv \
       --output /tmp/poe1-review.csv
     ```
     Otrzymasz `/tmp/poe1-review.csv` i `/tmp/poe1-review_events.csv`.
     Po wykryciu spójnej grupy zmian (przerwa krótsza niż 0,25 s) analiza
     wstrzymuje skanowanie, zapisuje klip od 1 s przed szczytem zmiany do 5 s
     po nim, a potem kontynuuje. Wyniki z opóźnień 1/3 klatek w tej samej
     grupie dają jeden klip. Domyślny katalog to
     `/opt/cameras/recordings/analysis/<data-z-katalogu-nagrania>/POE1_sikorki/`; można go
     zmienić przez `--clips-dir`. Klip trwa domyślnie 3 s (1 s przed + 2 s po
     szczycie; `--clip-before`, `--clip-after`). Po zapisaniu klipu analiza
     pomija kolejne 3 s (`--skip-after`), żeby sąsiednie klipy nie pokazywały
     tych samych momentów. Nazwa klipu zawiera czas zdarzenia relatywny od początku
     pliku w formacie `MM_SS`, np. `15-22-28_candidate_001_07_04.mkv`
     oznacza 7 min 4 s nagrania.
     W wierszu zdarzenia są czas początku/końca względem pliku, czas
     największej zmiany, rozmiar największego obszaru i liczba zmienionych
     pikseli. `--lags 1 3` (domyślnie; np. `--lags 1 3 5` daje czulszy, wolniejszy wariant); czasy są sekundami od początku
     nagrania. Współrzędne wielokąta można podać jawnie przez
     `--polygon x1 y1 x2 y2 x3 y3 ...`; domyślnie używany jest obrys powyżej.
     Domyślny obszar to ciemne podbicie dachu (współrzędne
     `(1088,605) (1318,564) (1462,1133) (1258,1187)` dla kadru 2560×1440),
     ustawione względem prawego dolnego rogu dachu. Kamera przesuwa się w ciągu
     dnia o kilka pikseli, więc każdą klatkę dopasowuje się wzorzec rogu
     (`mcrwac/analysis/reference/poe1_roof_corner.png`, krawędzie, okno ±8 px;
     co 5 s i po utracie okno ±60 px) i przesuwa wielokąt o różnicę ≥2 px
     (wtedy historia klatek jest czyszczona). Przy dopasowaniu słabszym niż 0,35
     (noc, mgła) zostaje ostatnie położenie. W logu: `Roof corner ... shift`.
     W podkatalogu `visualizations/` zapisywany jest też obraz ROI
     (`<nagranie>_roi.jpg`) oraz obraz szczytowej klatki każdego przedziału
     (`<nagranie>_lagN_candidate_...jpg`). W tych drugich czerwone piksele
     oznaczają różnicę jasności `>20`, zielony obszar to największy spójny
     komponent, a żółty obrys pokazuje maskowany ROI.
     Aby po kolei przetworzyć wszystkie pliki MKV z katalogu, podaj katalog
     zamiast pojedynczego nagrania (CSV i klipy trafią do katalogu analizy):
     ```bash
     ./venv_motion/bin/python -u -m mcrwac.analysis.poe1_bird_motion \
       /opt/cameras/recordings/POE1/2026-10-27 \
       --clips-dir /opt/cameras/recordings/analysis/2026-10-27/POE1_sikorki
     ```
     **Śnieżenie (świt/zmierzch/noc):** dla plików zachodzących na okolice
     wschodu/zachodu (wg `config/recording_config.yaml`, ±90 min) program
     mierzy udział zmienionych pikseli w wielokącie (mediana z 5 par klatek;
     próg 1,5%, czysty obraz dzienny <0,5%). Próbkuje co 60 s, a granice
     śnieg/czysto zawęża wyszukiwaniem binarnym (do ~2 s). Analizowane są
     tylko czyste fragmenty, w logu: `Noise scan ...`. Pliki z samego dnia
     pomija skan; `--no-noise-filter` go wyłącza.
   - Każdy przedział kandydujący jest oceniany na szczytowej klatce. Liczba
     zmienionych pikseli oznacza tu piksele, których jasność zmieniła się o
     ponad 20 poziomów. Program odrzuca zdarzenie, gdy ich liczba `> 5000`,
     gdy jest `500–5000` i największy spójny obszar stanowi `< 35%` tych zmian,
     albo gdy największy spójny obszar ma `> 2000 px`. Pozostałe zapisuje jako
     interesujące klipy. Decyzja, powód, liczba zmian i procentowy udział
     największego spójnego obszaru są w pliku `*_motion_events.csv` obok
     pomiarów klatka-po-klatce. Wiersze są posortowane po czasie zdarzenia;
     kolumna `clip_file` wskazuje klip (pusta dla odrzuconych), a
     `visualization_file` obraz w `visualizations/`. Są to progi eksperymentalne; np. bardzo bliska
     sikorka może odrzucić się przez dużą sumę zmian.
   - Czasy w CSV i klipach pochodzą ze znaczników prezentacji (PTS) klatek,
     nie z założenia `numer klatki / nominalne FPS`. Analiza i skan śnieżenia
     czytają klatki sekwencyjnie; pomijanie po zdarzeniu nie przelicza już
     sekund na indeks klatki. Czas trwania pliku pobierany jest przez `ffprobe`.
5. **Użyj gotowych elementów** z [squirrel_exit_prototype.py](mcrwac/analysis/squirrel_exit_prototype.py): wybór plików po dacie/godzinach (`select_recordings`, `--date`, `--hours`), `--watch` z listą `analyzed.txt`, klipy ffmpeg, `--frame-step`, logowanie `_log`, wzór usługi [squirrel-analysis.service](systemd/squirrel-analysis.service). Nie przenosić lokalizacji otworu (trójkąt komin–szczyt dachu i jego wzorce) ani założeń POE_BRAMA, jeśli kadr jest inny.
6. **Przejrzyj starsze moduły** `adaptive_dark_detector.py`, `blob_detector.py`, `astro_filter.py`, `segment*.py` i `scripts/motion_detect.py`. Mogą już dotyczyć POE1. Ich przydatności nie sprawdzano.

## Dokumentacja do utworzenia (analogicznie do POE_BRAMA)

- `README_POE1_ANALYSIS.md`: algorytm krok po kroku, uruchamianie, `--watch`, pliki wynikowe. Wzór: [README_SQUIRREL_ANALYSIS.md](README_SQUIRREL_ANALYSIS.md).
- `README_POE1_ANALYSIS_TECH.md`: architektura, stałe z uzasadnieniem, formaty plików, ograniczenia, tak aby dało się odtworzyć od zera. Wzór: [README_SQUIRREL_ANALYSIS_TECH.md](README_SQUIRREL_ANALYSIS_TECH.md).
- W [README.md](README.md) tylko krótka sekcja z odnośnikami do tych dwóch plików.
- Wyniki rozdzielone po analizie: `/opt/cameras/recordings/analysis/RRRR-MM-DD/POE1_sikorki/` (POE1) i `.../POE_BRAMA_wiewiorki/` (POE_BRAMA, razem z `analyzed.txt`).

## Pierwsza wiadomość do nowego agenta

> Cel POE1: <jedno zdanie>. Próbki: <ścieżki + opis, co w której sekundzie>.
> Przeczytaj README.md, README_SQUIRREL_ANALYSIS.md i README_SQUIRREL_ANALYSIS_TECH.md. Nie zakładaj, że POE1 ma otwór ani wzorce jak POE_BRAMA. Zacznij od zbadania kadru i próbek, potem zaproponuj najprostszą metodę i zmierz ją na próbkach. Dokumentację tworzymy w plikach `README_POE1_ANALYSIS.md` i `README_POE1_ANALYSIS_TECH.md`. Nie commituj bez mojej prośby.

## Obsługa usługi analizy POE1

Po każdym przeanalizowanym nagraniu usługa odświeża `result` ze wszystkich klipów dnia w
`.../POE1_sikorki/result_RRRR_MM_DD.mkv` (np. `result_2026_10_31.mkv`); to samo robi
usługa wiewiórek w `.../POE_BRAMA_wiewiorki/`.

Usługa `poe1-sikorki-analysis` uruchamia tryb `--watch`: analizuje kolejne
zakończone nagrania POE1 i zapisuje wyniki w
`/opt/cameras/recordings/analysis/RRRR-MM-DD/POE1_sikorki/`. Instalacja i start
(jednorazowa instalacja jednostki, później wystarczy start):

```bash
sudo cp systemd/poe1-sikorki-analysis.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now poe1-sikorki-analysis.service
```

Sprawdzenie statusu i tego, czy usługa działa:

```bash
sudo systemctl status poe1-sikorki-analysis.service --no-pager
systemctl is-active poe1-sikorki-analysis.service
```

Logi usługi z dzisiejszego dnia (według lokalnego czasu systemu):

```bash
sudo journalctl -u poe1-sikorki-analysis.service --since today --no-pager
```

Podgląd nowych wpisów na żywo oraz ostatnie 100 wpisów:

```bash
sudo journalctl -u poe1-sikorki-analysis.service -f
sudo journalctl -u poe1-sikorki-analysis.service -n 100 --no-pager
```

Po zmianie pliku jednostki wykonaj `sudo systemctl daemon-reload`, a następnie
zrestartuj usługę. Ręczne polecenia sterujące:

```bash
sudo systemctl restart poe1-sikorki-analysis.service
sudo systemctl stop poe1-sikorki-analysis.service
sudo systemctl start poe1-sikorki-analysis.service
```

Dzisiejsze wyniki i lista obsłużonych nagrań są w katalogu:

```bash
today=$(date +%F)
ls -lah "/opt/cameras/recordings/analysis/$today/POE1_sikorki/"
tail -n 30 "/opt/cameras/recordings/analysis/$today/POE1_sikorki/analyzed.txt"
```

`journalctl` pokazuje log działania procesu; `analyzed.txt` jest osobną listą
przetworzonych nagrań, a nie logiem systemd.
