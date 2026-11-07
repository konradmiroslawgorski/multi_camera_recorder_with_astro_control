# Część techniczna: analiza wyjścia wiewiórki

Uzupełnienie [README_SQUIRREL_ANALYSIS.md](README_SQUIRREL_ANALYSIS.md).
Analizator używa wyłącznie geometrii linii ścian i różnicy jasności dwóch
prostokątów. Nie wyszukuje komina ani dachu, nie śledzi obiektu, nie wykrywa
ruchu, nie odejmuje średniej jasności tła i nie pomija próbek z powodu skoku
jasności kamery.

## 1. Przepływ i architektura

1. Nagranie jest próbkowane raz na sekundę. Pomijane są wyłącznie klatki
   pośrednie, których nie analizujemy; nie jest to filtr skoków kamery.
2. Na każdej próbce `_measure_opening_brightness` szuka trzech krawędzi ścian
   i z ich przecięć wyznacza środek krzyżyka.
3. Program mierzy średnią jasność w polu testowym i polu odniesienia na
   nieprzetworzonej klatce w skali szarości.
4. Sygnał to bezwzględna różnica procentowa co najmniej 20%. `AppearanceDetector`
   potwierdza zdarzenie, gdy sygnał wystąpi w co najmniej 2 z 4 kolejnych
   próbek.
5. Zdarzenie zapisuje klip od pierwszej próbki spełniającej próg oraz wiersze
   pomiarów do tabulatorowego CSV. `--mode first` ogranicza liczbę klipów do
   jednego, ale nadal analizuje całe nagranie.

| Etap | Funkcje i ustawienia |
|---|---|
| Geometria | `_measure_opening_brightness`, `_fit_wall_line`, `_wall_intersection` |
| Pomiar i sygnał | `_analyze_brightness_video`, `AppearanceDetector`, `BRIGHTNESS_SAMPLE_SECONDS=1`, `CONFIRM_WINDOW_SAMPLES=4`, `CONFIRM_HITS=2` |
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
niebieską i żółtą; jeśli przecięcia różnią się o ponad 20 px, pomiar jest
odrzucany.

Pole testowe ma 10×17 px i lewy górny róg `(X−12, Y−19)`. Pole odniesienia ma
ten sam rozmiar, zaczyna się 2 px w lewo i znajduje się 5 pustych rzędów nad
pole testowym. Wartości jasności są średnimi pikseli po konwersji do skali
szarości. Nie ma rozmycia, odejmowania mediany ani adaptacyjnego tła.

Współrzędne pasm są skalibrowane dla widoku POE_BRAMA. Duże przesunięcie kamery
może spowodować błąd geometrii, który trafia do kolumny `status` CSV. W takim
przypadku próbka pozostaje w raporcie i nie wyzwala sygnału.

## 3. Sygnał i CSV

`DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT=20`. Dla poprawnego pomiaru sygnał
przekracza próg, gdy:

```text
abs(test_mean - reference_mean) / reference_mean * 100 >= próg
```

Wartość bezwzględna oznacza, że rozjaśnienie i przyciemnienie są traktowane
tak samo. Próbki z niepoprawną geometrią są liczone jako brak sygnału, ale nie
przerywają analizy filmu.

CSV jest kodowany jako UTF-8 z BOM-em i rozdzielany tabulatorami. Zawiera jeden
wiersz na sekundę, czas i numer klatki źródłowej, współrzędne krzyżyka i pól,
średnie jasności, różnicę ze znakiem i bezwzględną, różnicę procentową, sygnał
progu, potwierdzenie zdarzenia, początek klipu oraz status geometrii.

Klipy domyślnie trwają 60 sekund. Nakładające się zdarzenia nie tworzą
powielonych klipów. Materiał wideo jest kodowany przez ffmpeg (H.264, audio
AAC), a nazwa zawiera czas początku zdarzenia.

## 4. Kolejka i usługa

Tryb `--watch` wybiera zakończone, nieprzeanalizowane nagrania, czeka 120 s na
zamknięcie aktualnie zapisywanego segmentu i nadrabia zaległości po restarcie.
Wpisy statusu trafiają do `analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/analyzed.txt`.
Jednostka systemd to `systemd/squirrel-analysis.service`; instrukcje startu,
statusu i logów są w głównym README.

## 5. Testy i ograniczenia

Testy:

```bash
./venv_motion/bin/python -m unittest tests.test_squirrel_exit_prototype tests.test_notifier tests.test_time_window
```

Zmiana jasności jest sygnałem do utworzenia klipu, nie klasyfikacją gatunku.
Słońce, cień, ruch kamery lub obiekt w polu odniesienia również mogą przekroczyć
próg. Po zmianie kadru należy ponownie skalibrować ograniczone pasma wyszukiwania
linii.
