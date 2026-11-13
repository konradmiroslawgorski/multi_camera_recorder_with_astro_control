# Część techniczna: analiza wyjścia wiewiórki

Uzupełnienie [README_SQUIRREL_ANALYSIS.md](README_SQUIRREL_ANALYSIS.md).
Analizator używa wyłącznie geometrii linii ścian i różnicy jasności jednego
pola testowego względem jaśniejszego z dwóch prostokątów odniesienia. Nie
wyszukuje komina ani dachu, nie śledzi obiektu, nie wykrywa
ruchu, nie odejmuje średniej jasności tła i nie pomija próbek z powodu skoku
jasności kamery.

## 1. Przepływ i architektura

1. Nagranie jest próbkowane raz na sekundę. Po wykryciu program wraca do
   próbkowania po 2 s, na końcu klipu.
   Klatki pośrednie są nadal odczytywane przez dekoder, ale nie są analizowane.
   Wyjątkiem jest eksperymentalny `TestZmianyJasnosci`, który próbkuje bez
   przerw przez całe nagranie.
2. Starszy `squirrel_exit_prototype` na każdej próbce szuka trzech krawędzi
   ścian i z ich przecięć wyznacza środek krzyżyka. Jeśli bieżące linie są
   niewiarygodne, wykorzystuje ostatnią poprawną geometrię.
   `squirrel-analysis.service` kalibruje geometrię dnia z pomiarów o statusie
   `ok`: wybiera rzeczywisty pomiar najbliższy medianie współrzędnych krzyżyka
   i zapisuje go w `opening_geometry.json`. Kolejne nagrania tego dnia używają
   tej geometrii bez ponownego przesuwania pól między klatkami. Rozmiar pola
   testowego i jego przesunięcie względem krzyżyka pozostają bez zmian. Jeśli
   cache jeszcze nie istnieje, pierwsze nagranie wyznacza geometrię; wcześniejsze
   pliki bez poprawnego pomiaru mogą być ponowione po jej zapisaniu.
3. Program mierzy średnią jasność zielonego pola testowego i dwóch pól
   odniesienia na nieprzetworzonej klatce w skali szarości. Jako odniesienie
   wybiera większą z dwóch średnich.
4. `TestZmianyJasnosci` liczy niezależnie N=1 (P=0.05) i N=4 (P=0.1).
   Sygnał to wartość `((K[i+N] - K[i]) / K[i])²` równa lub większa od progu.
   Wystarczy jedna próbka spełniająca próg, aby wykryć zdarzenie; każdy wariant
   ma własny detektor, uzbrajający się ponownie po 1,5 s bez sygnału.
5. Klipy z obu wariantów, których przedziały czasowe się nakładają, są scalane
   do jednego klipu obejmującego sumę ich przedziałów. Dla N=1 klip trwa 5 s:
   od 2 s przed wykryciem do 3 s po nim. Dla N=4 trwa 7 s: od 6 s przed
   wykryciem do 1 s po nim. Krzyżyk i pole testowe są rysowane tylko przez
   pierwszą sekundę klipu. Następna próbka przypada na końcu klipu. Pomiary trafiają do skoroszytu Excel
   (`.xlsx`). Nazwa klipu zawiera zegarowy czas próbki wykrycia (zgodny z
   `czas_nagrania` w Excelu) i zmierzoną różnicę procentową z dokładnością do
   0,1 pp. Klipy watchera mają przez pierwszą sekundę nałożony żółty krzyżyk
   oraz zielony obrys pola testowego; obie adnotacje znikają po tej sekundzie.
   `--mode first` ogranicza liczbę klipów do jednego.

| Etap | Funkcje i ustawienia |
|---|---|
| Geometria | `_measure_opening_brightness`, `_fit_wall_line`, `_wall_intersection` |
| Pomiar i sygnał | `_analyze_brightness_video`, `AppearanceDetector`, `BRIGHTNESS_SAMPLE_SECONDS=1`, `Appearance.clip_seconds` |
| Klipy i wyniki | `_save_appearance_clips`, `_analyze_brightness_only`, `analyze_video` |
| Kolejka nagrań | `select_recordings`, `next_pending_recording`, `watch_recordings`, `main` |

## 2. Linie i prostokąty

W każdej klatce algorytm szuka:

- czerwonej, pionowej krawędzi narożnika: 10 próbek w zakresie Y=680–825 px,
  kandydaci X=346–368 px;
- niebieskiej, poziomej granicy po lewej: 10 próbek w X=278–340 px,
  kandydaci Y=738–775 px;
- żółtej granicy po prawej: po 5 próbek w zakresach X=362–386 i X=442–458 px,
  z lokalnym zakresem wyszukiwania wokół przewidywanej linii.

Każda krawędź wymaga co najmniej 5 punktów o gradiencie co najmniej 8. Dopasowanie
wykorzystuje konsensus par punktów do odrzucenia odstających kandydatów, a
następnie dopasowanie najmniejszych kwadratów. Rozrzut dopasowania nie może
przekroczyć 3 px. Środek krzyżyka jest średnią przecięć czerwonej linii z
niebieską i żółtą; jeśli przecięcia różnią się o ponad 20 px, geometria tej
próbki jest odrzucana. Gdy w bieżącej klatce nie da się ustalić poprawnej
geometrii, analizator używa współrzędnych linii i pól z ostatniej próbki, w
której geometria była poprawna, ale jasność nadal mierzy na bieżącej klatce.
Geometria jest aktualizowana tylko po jej ponownym poprawnym wyznaczeniu.
Pozwala to przetrwać chwilowe zasłonięcie linii przez szybko poruszający się
obiekt. Jeśli nie ma wcześniejszej poprawnej geometrii albo odziedziczone pola
nie mieszczą się w klatce, próbka pozostaje niezmierzona.

Pole testowe ma 12×22 px i lewy górny róg `(X−12, Y−19)`. Pola odniesienia
pozostają 10×17 px: pomarańczowe zaczyna się 2 px w lewo i znajduje się 5
pustych rzędów nad polem testowym; drugie leży bezpośrednio nad pomarańczowym,
z wyrównanym lewym brzegiem.
Wartości jasności są średnimi pikseli po konwersji do skali szarości. Nie ma
rozmycia, odejmowania mediany ani adaptacyjnego tła.

Współrzędne pasm są skalibrowane dla widoku POE_BRAMA. Przesunięcie kamery
może spowodować błąd geometrii; watcher stosuje zapisaną geometrię dnia i
oznacza jej użycie statusem `geometria_dzienna`. W nagraniu kalibrującym próbki
mierzone z odziedziczoną geometrią są oznaczone jako `poprzednia_geometria`,
a próbki bez możliwego pomiaru jako `blad_geometrii`.

## 3. Sygnał i skoroszyt Excel

`DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT=27`; skonfigurowany próg nie może być
niższy niż 27%. Dla poprawnego pomiaru sygnał osiąga próg, gdy:

```text
reference_mean = max(orange_reference_mean, upper_reference_mean)
abs(test_mean - reference_mean) / reference_mean * 100 >= próg
```

Próg jest włączny: dokładnie 27% wyzwala domyślny próg. Każdy wykryty sygnał,
niezależnie od jego siły, daje klip o tej samej długości: 1 s przed próbką i
2 s po niej.

Wartość bezwzględna oznacza, że rozjaśnienie i przyciemnienie są traktowane
tak samo. Próbki, których jasności nie da się zmierzyć nawet z użyciem
poprzedniej geometrii, są liczone jako brak sygnału, ale nie przerywają analizy
filmu.

Skoroszyt `.xlsx` ma arkusz `Pomiary` z jednym wierszem na każdą analizowaną
próbkę: zwykle co sekundę, z przerwą pomiarów równą długości klipu po wykryciu.
Zawiera czas i numer klatki źródłowej, współrzędne krzyżyka i trzech pól,
średnie jasności testowej i obu pól odniesienia, wybrane odniesienie, różnicę
ze znakiem i bezwzględną, różnicę procentową, sygnał progu,
potwierdzenie zdarzenia, początek klipu oraz status geometrii. Nagłówek
pozostaje widoczny podczas przewijania, a filtr obejmuje całą tabelę.

Klipy zaczynają się 1 s przed wykryciem i kończą 2 s po nim (łącznie 3 s).
Po wykryciu następna próbka przypada po 2 s. Jeśli wykrycie przypada przed
upływem pierwszej sekundy nagrania, klip zaczyna się
od jego pierwszej klatki. Nakładające się klipy nie są powielane. Materiał
wideo jest kodowany przez ffmpeg (H.264 `ultrafast`, CRF 20, audio AAC).
Każdy klip, zarówno z watchera, jak i starszej ścieżki analizy, pokazuje
krzyżyk i obrys pola testowego przez pierwszą sekundę.
Wyszukiwanie `-ss` odbywa się przed wejściem pliku z dokładnym seekiem, a log
zawiera czas wykonania ffmpeg dla każdego klipu. Nazwa zawiera zegarowy czas
próbki wykrycia i rzeczywistą różnicę jasności z tej próbki, np.
`06-10-39_06_24_34_22_7pct.mkv`; faktyczny początek klipu to 06:24:32.

## 4. Kolejka i usługa

Tryb `--watch` wybiera zakończone, nieprzeanalizowane nagrania, czeka 120 s na
zamknięcie aktualnie zapisywanego segmentu i nadrabia zaległości po restarcie.
Wpisy statusu trafiają do `analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/analyzed.txt`.
Jednostka `systemd/squirrel-analysis.service` uruchamia
`squirrel_brightness_tests --watch` z osobnymi N=1 (P=0.05) i N=4 (P=0.1).
Raporty dla obu N są osobne, a nakładające się klipy
są scalane. Raporty, klipy,
dzienny `result_RRRR_MM_DD.mkv` i `analyzed.txt` trafiają do
`analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/`. Instrukcje startu, statusu i logów
są w głównym README.

## 5. Testy i ograniczenia

Testy:

```bash
./venv_motion/bin/python -m unittest tests.test_squirrel_exit_prototype tests.test_notifier tests.test_time_window
```

Zmiana jasności jest sygnałem do utworzenia klipu, nie klasyfikacją gatunku.
Słońce, cień, ruch kamery lub obiekt w polu odniesienia również mogą przekroczyć
próg. Po zmianie kadru należy ponownie skalibrować ograniczone pasma wyszukiwania
linii.

## 6. Porównawcze testy jasności

`mcrwac.analysis.squirrel_brightness_tests` uruchamia wyłącznie
`TestZmianyJasnosci` (test `TestJasnosci` jest w tym przebiegu wyłączony).
Wyniki każdego wariantu trafiają do osobnego, unikalnego katalogu
`analysis/<data>/TestZmianyJasnosci_N<N>_<nazwa nagrania>_<uruchomienie>/`.
Jasność pola testowego jest próbkowana raz na sekundę, bez pomijania próbek po
wykryciach. Dla parametru N skrypt porównuje próbkę z próbką N sekund wcześniej:

```text
K = jasnosc_testowa_srednia_0_255
ZmianaJasnosci = ((K[i+N] - K[i]) / K[i])²
sygnał, gdy ZmianaJasnosci >= 0.030
```

Skoroszyt `*brightness*` ma osobny, pierwszy i aktywny arkusz `Wykres` z
wykresem zakotwiczonym w A1; dane są w arkuszu `Pomiary`. Wiersz wyniku jest
przypisany do późniejszej z porównywanych próbek. Domyślne domyślnie liczy N=3 (N=5 można wskazać w eksperymencie); konsola raportuje
postęp próbkowania co około 10%:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_brightness_tests \
  /ścieżka/do/nagrania.mkv --n 3 5
```
