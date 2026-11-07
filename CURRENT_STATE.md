# Aktualny stan projektu

Dokument opisuje stan kodu i środowiska zweryfikowany w chwili jego
przygotowania. Kod jest źródłem prawdy, jeśli opis w starszym pliku README
jest z nim sprzeczny. Dokument nie opisuje porzuconych wariantów algorytmów.

## Cel i architektura

Projekt uruchamia rejestrację dwóch kamer IP (`POE1` i `POE_BRAMA`) w linuksowej
maszynie VM i analizuje zakończone nagrania. Pliki źródłowe trafiają do
`/opt/cameras/recordings/<KAMERA>/<RRRR-MM-DD>/<GG-MM-SS>.mkv`, a wyniki
analizy do `/opt/cameras/recordings/analysis/<RRRR-MM-DD>/<podsystem>/`.

Rejestratory są niezależnymi usługami systemd. Obliczają dzienne okno
nagrywania z lokalnych danych astronomicznych, uruchamiają FFmpeg w segmentach
15-minutowych i zapisują segmenty do katalogów kamery. `POE_BRAMA` jest
przycinana do dolnej połowy, z czasem serwera wypalonym w obrazie; `POE1` jest
kopiowana bez tego przycięcia. Powiadomienia ntfy są opcjonalne i nie są
warunkiem nagrywania.

Analizatory działają oddzielnie od rejestratorów. Każdy może analizować
pojedyncze nagranie, datę lub działać jako obserwująca kolejkę usługa `--watch`.
Nie należy utożsamiać algorytmów POE1 i POE_BRAMA: detektor wiewiórek używa
różnicy jasności dwóch pól, a detektor POE1 nadal wyszukuje zmiany obrazu.

## Struktura istotnych plików

| Plik lub katalog | Rola |
|---|---|
| `mcrwac/core/recorder.py` | Harmonogram i nadzór nagrywania FFmpeg |
| `mcrwac/core/time_window.py`, `mcrwac/core/analysis_config.py` | Okna czasowe i konfiguracja |
| `mcrwac/analysis/squirrel_exit_prototype.py` | Pomiar jasności przy otworze, klipy i kolejka POE_BRAMA |
| `mcrwac/analysis/poe1_bird_motion.py` | Eksperymentalna analiza zmian obrazu POE1 |
| `mcrwac/analysis/reference/` | Wzorce obrazu używane przez detektor POE1 |
| `mcrwac/notify/` | Powiadomienia usług |
| `config/recording_config.yaml` | Lokalizacja, okno astronomiczne, długość segmentu i ustawienia powiadomień |
| `config/camera_*.env` | Ustawienia kamer; zawierają dane połączeniowe, nie kopiować ich do dokumentacji |
| `systemd/camera@.service` | Jednostka szablonowa rejestratora |
| `systemd/squirrel-analysis.service` | Usługa kolejki analizatora wiewiórek |
| `systemd/poe1-sikorki-analysis.service` | Usługa kolejki analizatora POE1 |
| `requirements.txt` | Zależności Pythona |
| `tests/` | Testy jednostkowe rejestracji, powiadomień, analizy POE1 i wiewiórek |

Pozostałe moduły `mcrwac/analysis/` zawierają dodatkowe narzędzia i prototypy;
nie są główną ścieżką uruchamianą przez wymienione jednostki analizy.

## Analizator wiewiórek — POE_BRAMA

Implementacja znajduje się w
[`mcrwac/analysis/squirrel_exit_prototype.py`](mcrwac/analysis/squirrel_exit_prototype.py).
Analizator używa wyłącznie geometrii krawędzi ścian i jasności dwóch
prostokątów. Nie używa detekcji ruchu, komina ani dachu, średniej jasności tła
ani odrzucania próbek po skoku jasności kamery. Nie wysyła powiadomień o
wykrytym zdarzeniu.

### Geometria i pomiar

- Nagranie jest próbkowane raz na sekundę; pomijane są tylko nieanalizowane
  klatki pośrednie.
- Na każdej próbce wyszukiwane są trzy krawędzie w skalibrowanych pasmach
  kadru POE_BRAMA: pionowa krawędź narożnika oraz lewa i prawa granica koloru
  ściany.
- Na krawędź przypada do 10 punktów. Dopasowanie konsensusem par punktów
  odrzuca odstające punkty, po czym prosta jest dopasowywana metodą
  najmniejszych kwadratów. Wymagane są co najmniej 5 punktów o gradiencie
  minimum 8; rozrzut dopasowania nie może przekraczać 3 px.
- Krzyżyk wyznacza średnia przecięć pionowej krawędzi z dwiema granicami
  ściany. Próbka jest odrzucana, jeśli przecięcia są odległe o ponad 20 px
  albo wynik jest poza dopuszczonym zakresem obrazu.
- Pole testowe ma rozmiar 10×17 px i lewy górny róg `(X−12, Y−19)`.
  Prostokąt odniesienia ma taki sam rozmiar, jest przesunięty o 2 px w lewo
  i ma 5 pustych rzędów nad polem testowym.
- Jasność jest średnią pikseli po konwersji do skali szarości. Nie ma
  rozmywania ani odejmowania tła.
- Sygnał to
  `abs(test_mean - reference_mean) / reference_mean * 100`.
  Domyślny próg wynosi 20%; wiewiórka nie jest klasyfikowana, a próg oznacza
  wyłącznie zmianę jasności.

Zdarzenie jest potwierdzone, gdy próg jest przekroczony w co najmniej 2 z
4 kolejnych próbek. Detektor uzbraja się ponownie po 1,5 s bez sygnału.
Domyślnie klip trwa 60 s i zaczyna się od pierwszej próbki spełniającej próg;
nakładające się klipy nie są powielane. `--mode all` zapisuje wszystkie
zdarzenia, a `--mode first` najwyżej pierwszy klip, lecz oba tryby analizują
całe nagranie. Wideo klipu jest kodowane jako H.264, audio jako AAC.

Każda próbka trafia do CSV kodowanego jako UTF-8 z BOM-em i rozdzielanego
tabulatorami. Raport zawiera czas i indeks klatki, położenie krzyżyka i pól,
średnie jasności, różnicę ze znakiem i bezwzględną, procent różnicy, stan
progu, potwierdzenie zdarzenia i status geometrii. Błąd geometrii zapisuje się
w kolumnie `status` i nie wyzwala sygnału.

### Kolejka i uruchamianie

Domyślne nagrania pochodzą z `/opt/cameras/recordings/POE_BRAMA`, a wyniki
z `analysis/<data>/POE_BRAMA_wiewiorki/`. `--watch` sprawdza kolejkę co 30 s;
segment jest zakończony, gdy pojawił się następny segment albo gdy bieżący
plik nie zmieniał się przez 120 s. Stan kolejki zapisuje się w `analyzed.txt`.
Usługa jest celowo niezależna od usługi kamery.

Przykłady:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  /ścieżka/do/nagrania.mkv
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  --date 2026-10-26 --hours 13:00 17:00
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype --watch
sudo systemctl status squirrel-analysis.service --no-pager
sudo journalctl -u squirrel-analysis.service --since today --no-pager
```

Wyniki i instrukcje obsługi usługi opisuje
[`README_SQUIRREL_ANALYSIS.md`](README_SQUIRREL_ANALYSIS.md), a szczegóły
implementacji [`README_SQUIRREL_ANALYSIS_TECH.md`](README_SQUIRREL_ANALYSIS_TECH.md).

### Kalibracja i ograniczenia

Pasma wyszukiwania linii są skalibrowane dla obecnego kadru POE_BRAMA. Zmiana
położenia kamery może powodować błędy geometrii i wymaga sprawdzenia lub
ponownej kalibracji. Przekroczenie progu może też wynikać ze słońca, cienia,
zmiany kadru lub obiektu w polu odniesienia; wynik nie jest potwierdzeniem
gatunku.

## Analizator POE1 — sikorki

`mcrwac/analysis/poe1_bird_motion.py` pozostaje osobnym, eksperymentalnym
detektorem zmian ruchu w wielokątnym ROI budki. Ten podsystem nadal korzysta
z dopasowania wzorca narożnika dachu, rozmycia obrazu w skali szarości i
porównywania klatek. Usunięcie tych metod z analizatora wiewiórek nie dotyczy
POE1.

Domyślny wielokąt ROI ma wierzchołki `(1088,605)`, `(1318,564)`,
`(1462,1133)`, `(1258,1187)`. Wzorzec narożnika jest wyszukiwany w promieniu
8 px, a po utracie dopasowania w promieniu 60 px; minimalny wynik dopasowania
to 0,35, a przesunięcie ROI następuje od 2 px. Obraz w skali szarości jest
rozmywany filtrem 3×3. Porównywane są klatki z opóźnieniem 1 i 3 klatek;
liczone są piksele o różnicy jasności ponad 12, 20 i 32 poziomy. Kandydaci
łączą się, gdy przerwa nie przekracza 0,25 s; minimalny spójny obszar to
domyślnie 150 px. Dalsza klasyfikacja jest heurystyczna, nie jest
rozpoznawaniem ptaka: kandydat jest odrzucany przy ponad 5000 zmienionych
pikselach, przy 500–5000 pikselach i spójności poniżej 35%, albo gdy największy
spójny obszar przekracza 2000 px. Czas klatek jest odczytywany sekwencyjnie
z PTS, a długość nagrania z `ffprobe`.

Filtr śnieżenia jest domyślnie włączony: próg zmienionych pikseli wynosi 1,5%,
sonda jest wykonywana co 60 s, a granica czystego obrazu zawężana do około
2 s. `--no-noise-filter` wyłącza filtr. Domyślny klip obejmuje 1 s przed i 2 s
po zdarzeniu; po kandydacie analiza pomija 3 s. Wyniki obejmują CSV pomiarów,
CSV zdarzeń, obrazy kontrolne i klipy.

Usługa `poe1-sikorki-analysis` uruchamia `--watch` dla
`/opt/cameras/recordings/POE1`; wyniki trafiają do
`analysis/<data>/POE1_sikorki/`. Aktualne zachowanie należy weryfikować w
module źródłowym. `README_poe1_sikorki.md` zawiera również tekst roboczy i
nie jest kompletną, zweryfikowaną dokumentacją techniczną.

## Zależności i sprawdzenie

Środowiskiem projektu jest `venv_motion` (Python 3.10.12 w chwili
weryfikacji). `requirements.txt` przypina:

- NumPy 2.2.6,
- OpenCV Python 4.13.0.92,
- Astral 3.2,
- PyYAML 6.0.3.

Analiza klipów wymaga systemowego `ffmpeg`; analiza POE1 korzysta także
z `ffprobe`. W chwili weryfikacji oba narzędzia były dostępne.

Testy wspólne i analizatora wiewiórek:

```bash
./venv_motion/bin/python -m unittest \
  tests.test_squirrel_exit_prototype tests.test_notifier tests.test_time_window
```

Weryfikacja w tej sesji: 35 testów zakończonych wynikiem `OK`; `--help`
analizatora wiewiórek pokazuje wyłącznie opcje aktualnego pomiaru jasności,
wyboru nagrań, trybu i obsługi kolejki. Testy POE1 są w
`tests/test_poe1_bird_motion.py`.

## Bieżący stan operacyjny i nierozstrzygnięte pozycje

- Obie usługi rejestratorów (`camera@POE1`, `camera@POE_BRAMA`) były aktywne
  podczas sprawdzenia. Usługi `squirrel-analysis` i
  `poe1-sikorki-analysis` były nieaktywne; nie działał proces analizatora
  wiewiórek. Nie uruchamiać ich bez osobnej decyzji.
- Ograniczona walidacja geometrii wiewiórek na 30 próbkach minutowych
  zakończyła się poprawnie. Podgląd pięciu przykładowych klatek znajduje się
  poza repozytorium w
  `/opt/cameras/recordings/analysis/2026-11-06/POE_BRAMA_wiewiorki/visualizations/brightness_geometry_zero_offset/zero_offset_line_geometry_06-40-53_samples.png`.
  Nie jest to walidacja całego nagrania.
- Plik `06-40-53_brightness_20261106064053.csv` zawiera 142 wiersze pomiarów
  (do 141 s) i jest niepełny. Nie ma kompletnego wyniku analizy tego pliku
  aktualnym algorytmem.
- `analysis/2026-11-06/POE_BRAMA_wiewiorki/analyzed.txt` zawiera dwa wpisy
  `06-40-53.mkv	ok`. Kod kolejki uznaje nazwę pliku za przeanalizowaną
  niezależnie od statusu i kompletności CSV, więc automatyczny `--watch`
  pominie ten plik. Wpisy i wynik należy uzgodnić przed ponowną analizą;
  ponowne przetwarzanie nie zostało uruchomione.
- Przed uznaniem detektora wiewiórek za skuteczny w eksploatacji pozostaje
  pełna analiza przykładowego nagrania, przegląd CSV/klipów i ocena wyników
  względem oznaczonych zdarzeń. Skuteczność POE1 również wymaga oceny na
  oznaczonych próbkach.
- W bieżącym drzewie roboczym są niezatwierdzone zmiany w analizatorze
  wiewiórek, jego testach i dwóch plikach README. Aktualny opis i testy
  odnoszą się do tych plików, a niekoniecznie do zawartości ostatniego commita.
