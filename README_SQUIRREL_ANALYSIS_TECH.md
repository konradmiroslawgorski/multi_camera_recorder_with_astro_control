# Część techniczna: analiza wyjścia wiewiórki

Uzupełnienie [README_SQUIRREL_ANALYSIS.md](README_SQUIRREL_ANALYSIS.md).
Analizator używa wyłącznie geometrii linii ścian i różnicy jasności jednego
pola testowego względem jaśniejszego z dwóch prostokątów odniesienia. Nie
wyszukuje komina ani dachu, nie śledzi obiektu, nie wykrywa
ruchu, nie odejmuje średniej jasności tła i nie pomija próbek z powodu skoku
jasności kamery.

## 1. Przepływ i architektura

1. Nagranie jest próbkowane raz na sekundę. Po wykryciu program wraca do
   próbkowania po dojściu do końca klipu: po 5, 7 lub 10 s od wykrycia.
   Klatki pośrednie są nadal odczytywane przez dekoder, ale nie są analizowane.
2. Na każdej próbce `_measure_opening_brightness` szuka trzech krawędzi ścian
   i z ich przecięć wyznacza środek krzyżyka.
3. Program mierzy średnią jasność zielonego pola testowego i dwóch pól
   odniesienia na nieprzetworzonej klatce w skali szarości. Jako odniesienie
   wybiera większą z dwóch średnich.
4. Sygnał to bezwzględna różnica procentowa równa lub większa od progu. Wystarczy
   jedna próbka spełniająca próg, aby wykryć zdarzenie; detektor uzbraja się
   ponownie po 1,5 s bez sygnału.
5. Długość fragmentu po wykryciu zależy od zmierzonej różnicy: >=30% — 10 s,
   >=25% — 7 s, >=22% — 5 s. Do każdego fragmentu dodawane są 2 s przed
   wykryciem, więc łączna długość klipu wynosi odpowiednio 12, 9 lub 7 s.
   Następna próbka przypada na koniec klipu. Wartości graniczne należą do
   odpowiadających im przedziałów. Pomiary trafiają do skoroszytu Excel
   (`.xlsx`). Nazwa klipu zawiera zegarowy czas próbki wykrycia (zgodny z
   `czas_nagrania` w Excelu) i zmierzoną różnicę procentową z dokładnością do
   0,1 pp.
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
niebieską i żółtą; jeśli przecięcia różnią się o ponad 20 px, pomiar jest
odrzucany.

Pole testowe ma 10×17 px i lewy górny róg `(X−12, Y−19)`. Pomarańczowe pole
odniesienia ma ten sam rozmiar, zaczyna się 2 px w lewo i znajduje się 5
pustych rzędów nad polem testowym. Drugie pole odniesienia o tym samym
rozmiarze i wyrównanym lewym brzegiem leży bezpośrednio nad pomarańczowym.
Wartości jasności są średnimi pikseli po konwersji do skali szarości. Nie ma
rozmycia, odejmowania mediany ani adaptacyjnego tła.

Współrzędne pasm są skalibrowane dla widoku POE_BRAMA. Duże przesunięcie kamery
może spowodować błąd geometrii, który trafia do kolumny `status` arkusza Excel.
W takim przypadku próbka pozostaje w raporcie i nie wyzwala sygnału.

## 3. Sygnał i skoroszyt Excel

`DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT=22`; skonfigurowany próg nie może być
niższy niż 22%. Dla poprawnego pomiaru sygnał osiąga próg, gdy:

```text
reference_mean = max(orange_reference_mean, upper_reference_mean)
abs(test_mean - reference_mean) / reference_mean * 100 >= próg
```

Próg jest włączny: dokładnie 22% wyzwala domyślny próg. Długość klipu
ustalana jest na podstawie rzeczywistej wartości pomiaru, a nie skonfigurowanego
progu: >=30% daje 10 s, >=25% daje 7 s, a >=22% daje 5 s.

Wartość bezwzględna oznacza, że rozjaśnienie i przyciemnienie są traktowane
tak samo. Próbki z niepoprawną geometrią są liczone jako brak sygnału, ale nie
przerywają analizy filmu.

Skoroszyt `.xlsx` ma arkusz `Pomiary` z jednym wierszem na każdą analizowaną
próbkę: zwykle co sekundę, z przerwą pomiarów równą długości klipu po wykryciu.
Zawiera czas i numer klatki źródłowej, współrzędne krzyżyka i trzech pól,
średnie jasności testowej i obu pól odniesienia, wybrane odniesienie, różnicę
ze znakiem i bezwzględną, różnicę procentową, sygnał progu,
potwierdzenie zdarzenia, początek klipu oraz status geometrii. Nagłówek
pozostaje widoczny podczas przewijania, a filtr obejmuje całą tabelę.

Klipy zaczynają się 2 s przed wykryciem. Całkowity czas trwania wynosi 7–12 s,
a klip kończy się na następnej próbce, wykonywanej 5, 7 lub 10 s po wykryciu.
Jeśli wykrycie przypada przed upływem pierwszych 2 s nagrania, klip zaczyna się
od jego pierwszej klatki. Nakładające się klipy nie są powielane. Materiał
wideo jest kodowany przez ffmpeg (H.264 `ultrafast`, CRF 20, audio AAC).
Wyszukiwanie `-ss` odbywa się przed wejściem pliku z dokładnym seekiem, a log
zawiera czas wykonania ffmpeg dla każdego klipu. Nazwa zawiera zegarowy czas
próbki wykrycia i rzeczywistą różnicę jasności z tej próbki, np.
`06-10-39_06_24_34_22_7pct.mkv`; faktyczny początek klipu to 06:24:32.

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
