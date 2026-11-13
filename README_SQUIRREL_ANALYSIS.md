# Analiza wyjścia wiewiórki (POE_BRAMA)

Część techniczna: [README_SQUIRREL_ANALYSIS_TECH.md](README_SQUIRREL_ANALYSIS_TECH.md). Ogólny opis projektu: [README.md](README.md).

Analizator w `mcrwac/analysis/squirrel_exit_prototype.py` przetwarza nagrania
kamery `POE_BRAMA` offline. Nie wysyła alarmów i nie jest podłączony do usług
nagrywających. Klipy wyzwala wyłącznie różnica jasności jednego małego pola
testowego i jaśniejszego z dwóch pól odniesienia przy otworze. Nie ma detekcji
ruchu ani wyszukiwania komina lub dachu.

Usługa `squirrel-analysis.service` używa odrębnego algorytmu
`TestZmianyJasnosci`: osobne detekcje dla N=1 (próg P=`0.05`) i N=4 (próg
P=`0.1`), bez uruchamiania `TestJasnosci`. Nakładające się klipy z obu N
są łączone, aby zdarzenie nie tworzyło dwóch klipów. Po kalibracji usługa
zapisuje jedną stabilną geometrię na kamerę i dzień, a kolejne nagrania mierzy
tym samym polem testowym 12×22 px. Opis niżej krok po kroku dotyczy ręcznego, starszego
`squirrel_exit_prototype`.

Dla N=1 klip trwa 5 s i zaczyna się 2 s przed wykryciem. Dla N=4 klip trwa
7 s: od 6 s przed wykryciem do 1 s po nim. Krzyżyk i pole testowe są rysowane
tylko przez pierwszą sekundę każdego klipu.

### Algorytm krok po kroku

1. **Linie ścian.** Domyślna analiza nie szuka komina ani szczytu dachu. Co
   sekundę wyznacza na klatce pionową krawędź narożnika i dwie linie granicy
   koloru ściany, dopasowując każdą z maksymalnie 10 próbek. Każda linia musi
   mieć co najmniej 5 zgodnych punktów, a błąd dopasowania nie może przekraczać
   3 px. Środek krzyżyka to średnia przecięć linii pionowej z obiema liniami
   poziomymi; gdy przecięcia są odległe o ponad 20 px, pomiar jest odrzucany.
2. **Pola pomiarowe.** Pole testowe ma 12×22 px, z lewym górnym rogiem
   `(X−12, Y−19)` względem krzyżyka. Pola odniesienia pozostają 10×17 px:
   pomarańczowe jest 2 px w lewo i 5 pustych pikseli nad polem testowym, a
   drugie leży bezpośrednio nad pomarańczowym, z wyrównanym lewym brzegiem.
   Wszystkie trzy pola są mierzone w skali szarości, bez rozmycia; do obliczeń
   wybierana jest większa średnia z dwóch pól odniesienia.
3. **Sygnał i klip.** Niech `odniesienie = max(odniesienie_pomarańczowe,
   odniesienie_górne)`. Sygnał występuje, gdy
   `abs(jasność_testu - odniesienie) / odniesienie * 100` jest równe 27% lub
   większe. Jedna próbka wystarcza do wykrycia. Każdy klip trwa 3 s: zaczyna
   się 1 s przed próbką wykrycia i kończy 2 s po niej. Następna próbka przypada
   2 s po wykryciu. Jeśli zdarzenie wypada przy początku lub końcu filmu, klip
   jest ograniczony do dostępnych klatek. `all` zapisuje wszystkie
   niepokrywające się klipy, `first` najwyżej pierwszy; oba tryby analizują
   całe nagranie poza przerwami po wykryciu.
Pomiar linii jest skalibrowany dla widoku `POE_BRAMA` i szuka krawędzi w
ograniczonych pasach obrazu. Gdy linie są chwilowo zakłócone przez ruch obiektu,
analizator użyje geometrii z ostatniej poprawnej próbki. W watcherze geometria
jest zapisywana dla całego dnia w `opening_geometry.json` i może być użyta w
innych nagraniach tego dnia. Gdy nie ma jeszcze geometrii dnia, skrypt najpierw
szuka pierwszej poprawnej linii w nagraniu i stosuje ją również do
wcześniejszych próbek. Nagranie bez żadnej poprawnej linii jest ponawiane po
wyznaczeniu geometrii w późniejszym pliku tego dnia. Jasność pól zawsze pochodzi
z bieżącej klatki. Status `poprzednia_geometria` w Excelu wskazuje użycie
zapasowej geometrii.
Przekroczenie progu oznacza zmianę jasności, a nie pewne rozpoznanie wiewiórki: wpływać mogą
też słońce, cień, przesunięcie kamery lub obiekt w polu odniesienia. Każda
sekundowa próbka jest oceniana; nie ma globalnego uśredniania tła ani odrzucania
próbek po skoku jasności kamery.

### Porównawcze testy jasności

Skrypt `mcrwac.analysis.squirrel_brightness_tests` uruchamia wyłącznie
`TestZmianyJasnosci` dla N=3 domyślnie (test `TestJasnosci` jest wyłączony).
Do porównania można jawnie podać `--n 3 5`. Skrypt mierzy pole testowe raz na sekundę i liczy
`((K[i+N] - K[i]) / K[i])²` z `jasnosc_testowa_srednia_0_255`; próg jest
`>= 0.030`. `TestZmianyJasnosci` próbuje bez przerw również po wykryciu.
Każdy wariant zapisuje osobny skoroszyt z widocznym arkuszem wykresu
otwieranym przy A1. Po niezależnym wykryciu zdarzeń dla wszystkich N
nakładające się przedziały klipów są łączone i zapisywane tylko raz. Postęp
próbkowania jest wypisywany co ok. 10%.

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_brightness_tests \
  /ścieżka/do/nagrania.mkv --n 3
```

### Ponowna analiza nagrania 06-19-47.mkv

Uruchom z katalogu repozytorium, podając dodatnie N, na przykład:

```bash
./venv_motion/bin/python -m scripts.analyze_squirrel_061947 3
```

Skrypt przed analizą usuwa tylko raporty i klipy zaczynające się od
`06-19-47_` w katalogu `analysis/2026-11-11/POE_BRAMA_wiewiorki/`, po czym
odświeża dzienny `result_2026_11_11.mkv`. Nie usuwa dziennej geometrii
`opening_geometry.json` ani listy `analyzed.txt`. Zmień argument `3` na
wybrane N, np. `5`, aby wykonać inny wariant. Opcjonalny drugi argument ustawia
próg P; domyślnie jest to `0.030`:

```bash
./venv_motion/bin/python -m scripts.analyze_squirrel_061947 5 0.1
```

P jest porównywane bezpośrednio z wartością `ZmianaJasnosci`, nie z procentem.

Watcher usługi używa tej samej analizy dla kolejki zakończonych nagrań:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_brightness_tests \
  --watch --from-date 2026-11-11 --n 1 4 --p 0.05 0.1
```

### Uruchamianie

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  /ścieżka/do/nagrania.mkv
```

Przykład dla nagrania z serwera:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  /opt/cameras/recordings/POE_BRAMA/2026-10-25/05-49-02-5-25.mkv
```

Domyślnie analiza przetwarza cały film i zapisuje wszystkie wykryte pojawienia
się (`--mode all`). Aby zapisać tylko pierwszy klip, użyj `--mode first`.
Jasnościowy próg można dostroić opcją `--brightness-threshold-percent`
(domyślnie 27; dozwolone wartości to 27 lub więcej), na przykład:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  /ścieżka/do/nagrania.mkv --mode all \
  --brightness-threshold-percent 27
```

Aby przeanalizować wiele nagrań z jednego dnia, użyj `--date` (zamiast pliku) i
opcjonalnie `--hours`. Bez `--hours` analizowane są wszystkie nagrania z dnia;
z jedną godziną — od niej do końca dnia; z dwiema — od pierwszej do drugiej.
Porównywana jest godzina startu z nazwy pliku (`GG-MM-SS.mkv`), a godzina końca
jest wyłączona (plik zaczynający się dokładnie o niej nie wchodzi). Pliki
pochodne, np. `05-49-02-18-19.mkv`, są pomijane. Błąd jednego pliku nie
przerywa pozostałych. Kamerę (`--camera`, domyślnie `POE_BRAMA`) i katalog
nagrań (`--recordings-root`) można zmienić.

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  --date 2026-10-26 --hours 13:00 17:00
```

#### Analiza na bieżąco (`--watch`)

`--watch` działa ciągle i analizuje **każde nagranie zaraz po jego
zakończeniu**, jedno po drugim (kolejka od najstarszego). Plik jest uznany za
zakończony, gdy w katalogu dnia jest już nowszy segment albo gdy nie był
zapisywany od 2 minut — dlatego ostatni plik dnia (po wyłączeniu nagrywania
przez astro wieczorem) też zostanie przeanalizowany, bez osobnego wyzwalacza.
Zaległości (np. po restarcie) są nadrabiane; Dni liczą się od uruchomienia, więc analiza przechodzi przez północ.
`--from-date RRRR-MM-DD` pozwala wznowić zaległości od wcześniejszej daty;
bez niego kolejka zaczyna od dziś. `--hours` ogranicza zakres godzin każdego
dnia.
Proces ma obniżony priorytet (`--nice`, domyślnie 10), by nie dławić
rejestratora.

Watcher zapisuje listę przeanalizowanych plików w
`analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/analyzed.txt` (nazwa i `ok` albo
`blad`). Wpis chroni przed ponowną analizą; aby powtórzyć plik, usuń jego
wiersz. Wyniki serwisu trafiają bezpośrednio do
`analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/`, razem z `analyzed.txt`. Katalog
zawiera raporty, klipy i dzienny `result_RRRR_MM_DD.mkv`.

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_brightness_tests \
  --watch --n 1 4 --p 0.05 0.1
```

Jako usługa (jak rejestrator, jednorazowa instalacja na serwerze):

```bash
sudo cp systemd/squirrel-analysis.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now squirrel-analysis.service
journalctl -u squirrel-analysis -f
```

### Pliki wynikowe

Zarówno ręczny analizator, jak i serwis zapisują wyniki do
`analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/`. Serwis używa `TestZmianyJasnosci`
z osobnymi N=1 (P=0.05) i N=4 (P=0.1); dla nakładających się zdarzeń
zapisuje jeden klip. Każdy skoroszyt ma aktywny arkusz `Wykres` z wykresem
przy A1 i arkusz `Pomiary`.

W ręcznym analizatorze nazwa skoroszytu zawiera datę i godzinę startu nagrania;
nazwa klipu zawiera zegarowy czas próbki wykrycia i zmierzoną różnicę:

| Plik | Nazwa | Zawartość |
|------|-------|-----------|
| Excel jasności (zawsze) | `<nagranie>_brightness_<czas startu nagrania>.xlsx` | skoroszyt z arkuszem `Pomiary`; jeden wiersz na analizowaną próbkę (zwykle co sekundę, z przerwą 2 s po wykryciu): krzyżyk, współrzędne trzech pól, jasności obu odniesień i wybrane odniesienie; nagłówek jest zamrożony, a tabela ma filtr |
| Klip | `<nagranie>_<czas_zegarowy_HH_MM_SS>_<zmierzona_różnica>pct.mkv` | fragment 3 s, 1 s przed i 2 s po próbce wykrycia; przez pierwszą sekundę klipu widoczne są krzyżyk i pole testowe; długość nie zależy od siły sygnału; wideo H.264 (`ultrafast`, CRF 20), dźwięk AAC; log podaje czas zapisu każdego klipu |

Nazwa klipu zawiera zegarowy czas próbki wykrycia (zgodny z `czas_nagrania` w
Excelu), nie wcześniejszego początku fragmentu, oraz rzeczywistą różnicę
jasności z tej próbki. Przykład: próbka o 06:24:34 z różnicą 24,7% daje
`06-10-39_06_24_34_24_7pct.mkv`; fragment zaczyna się o 06:24:33. Czas jest
obcinany do pełnych sekund, a procent zaokrąglany do jednego miejsca.
Skoroszyt zachowuje datę i godzinę nagrania w nazwie. Ponowne analizy nie
nadpisują wyników — dostają sufiks, np. `_02`. Opcja `--output` wskazuje inną
bazową ścieżkę; skoroszyt Excel i klipy trafiają do jej katalogu.

Watcher po każdym przeanalizowanym nagraniu odświeża dzienny plik
`result_RRRR_MM_DD.mkv` w katalogu `POE_BRAMA_wiewiorki`.

### Obsługa usługi i logów

Jednostkę instaluje się jednorazowo z katalogu głównego repozytorium. Polecenie
włącza usługę teraz i przy każdym starcie systemu:

```bash
sudo cp systemd/squirrel-analysis.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now squirrel-analysis.service
```

Sprawdzenie statusu i tego, czy usługa działa:

```bash
sudo systemctl status squirrel-analysis.service --no-pager
systemctl is-active squirrel-analysis.service
```

Logi usługi z dzisiejszego dnia (według lokalnego czasu systemu):

```bash
sudo journalctl -u squirrel-analysis.service --since today --no-pager
```

Podgląd nowych wpisów na żywo oraz ostatnie 100 wpisów:

```bash
sudo journalctl -u squirrel-analysis.service -f
sudo journalctl -u squirrel-analysis.service -n 100 --no-pager
```

Po zmianie pliku jednostki wykonaj `sudo systemctl daemon-reload`, a następnie
zrestartuj usługę. Ręczne polecenia sterujące:

```bash
sudo systemctl restart squirrel-analysis.service
sudo systemctl stop squirrel-analysis.service
sudo systemctl start squirrel-analysis.service
```

Dzisiejsze wyniki i lista obsłużonych nagrań są w katalogu:

```bash
today=$(date +%F)
ls -lah "/opt/cameras/recordings/analysis/$today/POE_BRAMA_wiewiorki/"
tail -n 30 "/opt/cameras/recordings/analysis/$today/POE_BRAMA_wiewiorki/analyzed.txt"
```

`journalctl` pokazuje log działania procesu; `analyzed.txt` jest osobną listą
przetworzonych nagrań, a nie logiem systemd.
