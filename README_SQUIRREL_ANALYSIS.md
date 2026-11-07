# Analiza wyjścia wiewiórki (POE_BRAMA)

Część techniczna: [README_SQUIRREL_ANALYSIS_TECH.md](README_SQUIRREL_ANALYSIS_TECH.md). Ogólny opis projektu: [README.md](README.md).

Analizator w `mcrwac/analysis/squirrel_exit_prototype.py` przetwarza nagrania
kamery `POE_BRAMA` offline. Nie wysyła alarmów i nie jest podłączony do usług
nagrywających. Klipy wyzwala wyłącznie różnica jasności jednego małego pola
testowego i jaśniejszego z dwóch pól odniesienia przy otworze. Nie ma detekcji
ruchu ani wyszukiwania komina lub dachu.

### Algorytm krok po kroku

1. **Linie ścian.** Domyślna analiza nie szuka komina ani szczytu dachu. Co
   sekundę wyznacza na klatce pionową krawędź narożnika i dwie linie granicy
   koloru ściany, dopasowując każdą z maksymalnie 10 próbek. Każda linia musi
   mieć co najmniej 5 zgodnych punktów, a błąd dopasowania nie może przekraczać
   3 px. Środek krzyżyka to średnia przecięć linii pionowej z obiema liniami
   poziomymi; gdy przecięcia są odległe o ponad 20 px, pomiar jest odrzucany.
2. **Pola pomiarowe.** Pole testowe ma 10×17 px, z lewym górnym rogiem
   `(X−12, Y−19)` względem krzyżyka. Pomarańczowe pole odniesienia ma ten sam
   rozmiar, jest 2 px w lewo i 5 pustych pikseli nad polem testowym. Drugie
   pole odniesienia o tym samym rozmiarze leży bezpośrednio nad pomarańczowym,
   z wyrównanym lewym brzegiem. Wszystkie trzy pola są mierzone w skali
   szarości, bez rozmycia; do obliczeń wybierana jest większa średnia z dwóch
   pól odniesienia.
3. **Sygnał i klip.** Niech `odniesienie = max(odniesienie_pomarańczowe,
   odniesienie_górne)`. Sygnał występuje, gdy
   `abs(jasność_testu - odniesienie) / odniesienie * 100` jest równe progowi
   lub większe. Jedna próbka wystarcza do wykrycia. Długość
   klipu i przerwa w próbkowaniu zależą od zmierzonej różnicy:

   | Różnica | Długość całego klipu | Następna próbka |
   |---------|---------------------|-----------------|
   | >=30% | 12 s | 10 s po wykryciu |
   | >=25% | 9 s | 7 s po wykryciu |
   | >=22% | 7 s | 5 s po wykryciu |

   Granice są włączne: dokładnie 30% daje klip 12 s, dokładnie 25% — 9 s,
   a dokładnie 22% — 7 s. Klip zaczyna się 2 s przed wykryciem i kończy wraz
   z następną próbką. Po wykryciu analizator pomija próbki do końca klipu,
   a potem wraca do próbkowania co sekundę. Jeśli zdarzenie wypada na początku
   filmu, dostępny przedział przed wykryciem jest krótszy.
   Nazwa klipu zawiera rzeczywistą różnicę z próbki wykrycia, zaokrągloną do
   0,1 punktu procentowego. `all` zapisuje wszystkie niepokrywające się klipy,
   `first` najwyżej pierwszy; oba tryby analizują całe nagranie poza przerwami
   po wykryciu.
Pomiar linii jest skalibrowany dla widoku `POE_BRAMA` i szuka krawędzi w
ograniczonych pasach obrazu. Po większym przesunięciu kamery może nie znaleźć
poprawnej geometrii; błąd będzie widoczny w kolumnie `status` arkusza Excel.
Przekroczenie progu oznacza zmianę jasności, a nie pewne rozpoznanie wiewiórki: wpływać mogą
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
(domyślnie 22; dozwolone wartości to 22 lub więcej), na przykład:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  /ścieżka/do/nagrania.mkv --mode all \
  --brightness-threshold-percent 22
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

Lista przeanalizowanych plików to `analysis/RRRR-MM-DD/POE_BRAMA_wiewiorki/analyzed.txt` (nazwa i
`ok` albo `blad`); wpisuje ją też zwykłe uruchomienie `--date`/plik bez
`--output`. Wpis chroni przed ponowną analizą — aby powtórzyć plik, usuń jego
wiersz. Plik z błędem trafia do listy jako `blad` i nie jest ponawiany.

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype --watch
```

Przykład wznowienia zaległości od 5 listopada 2026:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  --watch --from-date 2026-11-05
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
tworzony automatycznie). Nazwa skoroszytu zawiera datę i godzinę startu
nagrania; nazwa klipu zawiera zegarowy czas próbki wykrycia i zmierzoną
różnicę:

| Plik | Nazwa | Zawartość |
|------|-------|-----------|
| Excel jasności (zawsze) | `<nagranie>_brightness_<czas startu nagrania>.xlsx` | skoroszyt z arkuszem `Pomiary`; jeden wiersz na analizowaną próbkę (zwykle co sekundę, z przerwą 5/7/10 s po wykryciu do końca klipu): krzyżyk, współrzędne trzech pól, jasności obu odniesień i wybrane odniesienie; nagłówek jest zamrożony, a tabela ma filtr |
| Klip | `<nagranie>_<czas_zegarowy_HH_MM_SS>_<zmierzona_różnica>pct.mkv` | fragment 7–12 s, zaczyna się 2 s przed próbką wykrycia i kończy przy następnej próbce; długość zależy od zmierzonej różnicy; wideo H.264 (`ultrafast`, CRF 20), dźwięk AAC; log podaje czas zapisu każdego klipu |

Nazwa klipu zawiera zegarowy czas próbki wykrycia (zgodny z `czas_nagrania` w
Excelu), nie wcześniejszego początku fragmentu, oraz rzeczywistą różnicę
jasności z tej próbki. Przykład: próbka o 06:24:34 z różnicą 22,7% daje
`06-10-39_06_24_34_22_7pct.mkv`; fragment zaczyna się o 06:24:32. Czas jest
obcinany do pełnych sekund, a procent zaokrąglany do jednego miejsca.
Skoroszyt zachowuje datę i godzinę nagrania w nazwie. Ponowne analizy nie
nadpisują wyników — dostają sufiks, np. `_02`. Opcja `--output` wskazuje inną
bazową ścieżkę; skoroszyt Excel i klipy trafiają do jej katalogu.

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
