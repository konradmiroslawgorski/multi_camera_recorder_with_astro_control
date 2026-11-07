# Analiza wyjścia wiewiórki (POE_BRAMA)

Część techniczna: [README_SQUIRREL_ANALYSIS_TECH.md](README_SQUIRREL_ANALYSIS_TECH.md). Ogólny opis projektu: [README.md](README.md).

Analizator w `mcrwac/analysis/squirrel_exit_prototype.py` przetwarza nagrania
kamery `POE_BRAMA` offline. Nie wysyła alarmów i nie jest podłączony do usług
nagrywających. Klipy wyzwala wyłącznie różnica jasności między dwoma małymi
polami przy otworze. Nie ma detekcji ruchu ani wyszukiwania komina lub dachu.

### Algorytm krok po kroku

1. **Linie ścian.** Domyślna analiza nie szuka komina ani szczytu dachu. Co
   sekundę wyznacza na klatce pionową krawędź narożnika i dwie linie granicy
   koloru ściany, dopasowując każdą z maksymalnie 10 próbek. Każda linia musi
   mieć co najmniej 5 zgodnych punktów, a błąd dopasowania nie może przekraczać
   3 px. Środek krzyżyka to średnia przecięć linii pionowej z obiema liniami
   poziomymi; gdy przecięcia są odległe o ponad 20 px, pomiar jest odrzucany.
2. **Pola pomiarowe.** Pole testowe ma 10×17 px, z lewym górnym rogiem
   `(X−12, Y−19)` względem krzyżyka. Pole odniesienia ma ten sam rozmiar,
   jest 2 px w lewo i 5 pustych pikseli nad polem testowym. Oba pola są
   mierzone w skali szarości, bez rozmycia i odejmowania tła.
3. **Sygnał i klip.** Sygnał przekracza próg, gdy
   `abs(jasność_testu - jasność_odniesienia) / jasność_odniesienia * 100 >= 20%`.
   Klip powstaje po potwierdzeniu sygnału w co najmniej 2 z 4 kolejnych próbek;
   jego początek przypada na pierwszą próbkę spełniającą próg. Nakładające się
   60-sekundowe klipy nie są powielane. `all` zapisuje wszystkie zdarzenia,
   `first` najwyżej pierwszy klip; oba tryby tworzą pomiar całego nagrania.
Pomiar linii jest skalibrowany dla widoku `POE_BRAMA` i szuka krawędzi w
ograniczonych pasach obrazu. Po większym przesunięciu kamery może nie znaleźć
poprawnej geometrii; błąd będzie widoczny w kolumnie `status` CSV. Przekroczenie
progu oznacza zmianę jasności, a nie pewne rozpoznanie wiewiórki: wpływać mogą
też słońce, cień, przesunięcie kamery lub obiekt w polu odniesienia. Każda
sekundowa próbka jest oceniana; nie ma globalnego uśredniania tła ani odrzucania
próbek po skoku jasności kamery.

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
(domyślnie 20), a długość klipu opcją `--clip-seconds`, na przykład:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  /ścieżka/do/nagrania.mkv --mode all \
  --brightness-threshold-percent 20 --clip-seconds 120
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
Zaległości (np. po restarcie) są nadrabiane; dni liczą się od uruchomienia, więc
analiza przechodzi przez północ. `--hours` ogranicza zakres godzin każdego dnia.
Proces ma obniżony priorytet (`--nice`, domyślnie 10), by nie dławić
rejestratora.

Lista przeanalizowanych plików to `analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/analyzed.txt` (nazwa i
`ok` albo `blad`); wpisuje ją też zwykłe uruchomienie `--date`/plik bez
`--output`. Wpis chroni przed ponowną analizą — aby powtórzyć plik, usuń jego
wiersz. Plik z błędem trafia do listy jako `blad` i nie jest ponawiany.

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype --watch
```

Jako usługa (jak rejestrator, jednorazowa instalacja na serwerze):

```bash
sudo cp systemd/squirrel-analysis.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now squirrel-analysis.service
journalctl -u squirrel-analysis -f
```

### Pliki wynikowe

Wszystko trafia domyślnie do podkatalogu z datą nagrania,
`/opt/cameras/recordings/analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/` (np. `analysis/2026-10-26/`;
tworzony automatycznie), a nazwy mają postać
`<nazwa nagrania>_<...>_RRRRMMDDGGMMSS`:

| Plik | Nazwa | Zawartość |
|------|-------|-----------|
| CSV jasności (zawsze) | `<nagranie>_brightness_<czas startu nagrania>.csv` | tabulatorowy; jeden wiersz na sekundę: krzyżyk, współrzędne obu pól, średnie jasności, różnica ze znakiem i bezwzględna, różnica procentowa, sygnał ≥ progu i zdarzenie potwierdzone |
| Klip | `<nagranie>_<czas zdarzenia>.mkv` | fragment źródła od pierwszej próbki spełniającej próg (domyślnie 60 s), wideo H.264, dźwięk AAC |

Czas w nazwie (`RRRRMMDDGGMMSS`, np. `20261026133021`) liczony jest z katalogu
daty i godziny w nazwie pliku (`2026-10-26/13-00-08.mkv`); dla klipu dodatkowo
o przesunięcie zdarzenia w filmie. Przykład: zdarzenie 30 s po starcie
`13-00-08.mkv` daje `13-00-08_20261026130038.mkv`. Gdy nazwy nie da się
odczytać, czas startu wynika z daty modyfikacji pliku. Ponowne analizy nie
nadpisują wyników — dostają sufiks, np. `_02`. Opcja `--output` wskazuje inną
bazową ścieżkę; CSV i klipy trafiają do jej katalogu.

W trybie `--watch` (usługa) po każdym przeanalizowanym nagraniu program skleja klipy dnia
(`*.mkv` w kolejności czasu) w `result_RRRR_MM_DD.mkv` w tym samym katalogu
(np. `result_2026_10_31.mkv`). Plik jest odświeżany, gdy pojawią się nowsze
klipy. Tak samo dla sikorek (`.../POE1_sikorki/`).

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
