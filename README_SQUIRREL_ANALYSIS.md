# Analiza wyjścia wiewiórki (POE_BRAMA)

Część techniczna: [README_SQUIRREL_ANALYSIS_TECH.md](README_SQUIRREL_ANALYSIS_TECH.md). Ogólny opis projektu: [README.md](README.md).

Prototyp w `mcrwac/analysis/squirrel_exit_prototype.py` offline'owo analizuje
nagrania kamery `POE_BRAMA` i wykrywa moment, gdy z małego otworu w ścianie
wygląda wiewiórka. Nie wysyła alarmów i nie jest podłączony do usług
nagrywających. Wiewiórka nie jest rozpoznawana jako gatunek: program wykrywa
**dużą, ciemną zmianę obrazu tuż przy otworze**.

### Algorytm krok po kroku

Poniższe wartości (piksele dla nagrań 2304×1296, 12 kl./s) są stałymi na górze
pliku prototypu.

1. **Szukanie otworu (trójkąt podobny).** Program nie szuka samego otworu, bo
   jest mały i bywa niewidoczny (słońce, mgła, noc). Zamiast tego znajduje dwa
   punkty, które zawsze są widoczne na tle nieba: **lewą krawędź komina** i
   **szczyt dachu**. Ich wzorce (małe wycinki krawędzi, nie jasności) leżą w
   [`mcrwac/analysis/reference/`](mcrwac/analysis/reference/):
   `poe_brama_chimney.png` i `poe_brama_ridge.png`. Cień na ścianie ani drzewa
   ich nie zasłaniają, więc metoda działa przy każdym oświetleniu. Wzorce są
   dopasowywane do całej klatki w skalach 0,85–1,18. Odległość i kąt między
   znalezionymi punktami dają skalę i obrót względem wzorca, a otwór jest
   **trzecim wierzchołkiem trójkąta podobnego** (komin–szczyt–otwór) ze wzorca,
   przeniesionego na bieżącą klatkę; nie musi być widoczny. Warunki: wynik
   dopasowania każdego z dwóch punktów ≥ 0,58, skala trójkąta 0,85–1,18, obrót
   do 15°, wynik w kadrze. Z klatek próbkowanych co 0,25 s przez 3 s (13 klatek)
   wynik jest przyjęty, gdy powstaje w co najmniej połowie klatek (rozrzut do
   12 px). Działa także po restarcie lub przesunięciu kamery. Uwaga: wzorce
   pochodzą z `11-52-43.mkv` (klatka 0, otwór ≈ (347, 744)) i są dopasowane do
   widoku kamery `POE_BRAMA`.
2. **Komin lub szczyt niewidoczny — szukanie siatką i bisekcją.** Przy
   ciemności lub mgle (np. nocą komin widać dopiero pod koniec nagrania) program
   nie kończy się błędem. Zamiast sprawdzać film co 15 s sprawdza **jedną
   klatkę co 60 s** (ok. 1,5 s na klatkę). Po pierwszej udanej klatce wykonuje
   **bisekcję** między nią a ostatnią klatką bez trójkąta, do dokładności 15 s,
   i potwierdza wynik krokiem 1 (13 klatek). Analiza rusza od tego momentu, więc
   nie traci początku. Gdy nie znajdzie trójkąta w całym pozostałym fragmencie,
   kończy plik bez wyników. Założenie: punkty są widoczne od jednego momentu w
   danym przedziale 60 s; krótsze okna widoczności mogą zostać pominięte.
3. **Śledzenie.** Dla każdej analizowanej klatki program dopasowuje wzorzec otoczenia otworu
   (`matchTemplate`, okno ±48 px, szukanie ±32 px) i koryguje położenie, więc
   drobne ruchy kamery nie przeszkadzają. Klatki o pewności < 0,60 są pomijane.
   Po ok. 15 s takich klatek otwór uznaje się za utracony i program wraca do
   kroku 1, szukając od miejsca utraty, aż komin i szczyt dachu będą znowu
   widoczne.
5. **Tło.** Obszar analizy to okno wokół otworu: ±45 px w poziomie, 60 px w górę
   i 80 px w dół. Jego tło startuje z klatki odniesienia i jest powoli
   aktualizowane (współczynnik 0,01), więc zmiany oświetlenia nie dają alarmu.
   Piksele, w których wykryto zmianę, są zamrożone (wiewiórka nie wtapia się w
   tło), ale zmiana trwająca ponad 30 s uchodzi za trwałą i trafia do tła.
6. **Maska zmiany.** Od tła odejmowana jest globalna zmiana ekspozycji (mediana
   różnicy), po czym liczy się **tylko to, co wyraźnie ciemniejsze od tła**:
   piksel musi być ciemniejszy o ponad 25 poziomów jasności **i** mieć mniej niż
   65% jasności tła. Drugi warunek odrzuca cienie, zmiany słońca i ekspozycji,
   które tylko nieco przyciemniają jasną ścianę (wiewiórka jest wielokrotnie
   ciemniejsza); rozjaśnienia i szum są pomijane.
7. **Filtry regionów.** Zmiany są otaczane prostokątami. Prostokąt jest brany
   pod uwagę, gdy jego **przekątna > 30 px** (`--min-diagonal-px`), **krótszy
   bok ≥ 12 px** (odrzuca cienkie drgania krawędzi) i **proporcje ≤ 6:1**
   (odrzuca długie pasy, np. krawędzie elewacji). Mniejsze obszary są
   odrzucane.
8. **Potwierdzenie.** Analizowana jest co 4. klatka (`--frame-step`, ok. 3 próbki
   na sekundę; `1` = każda klatka, wolniej). Pojawienie się wiewiórki jest
   zgłaszane, gdy w ≥ 2 z ostatnich 3 próbek (przy `--frame-step 1`: ≥ 3 z 5
   klatek) jest co najmniej jeden taki prostokąt. Początkiem zdarzenia jest
   pierwsza z tych klatek. Kolejne zdarzenie jest możliwe dopiero po ≥ 1,5 s
   bez dużego regionu. Skutek rzadszej analizy: bardzo krótkie przeloty
   (poniżej ok. 0,7 s) mogą zostać pominięte.
9. **Tryby.** `first` (domyślny) kończy analizę po pierwszym zdarzeniu; `all`
   analizuje cały film i zapisuje każde pojawienie się.
10. **Wyniki.** Dla każdego zdarzenia ffmpeg wycina klip od początku zdarzenia
    (domyślnie 180 s, `--clip-seconds`; krótszy, jeśli film się kończy). Pełny
    podgląd MP4 powstaje tylko z `--save-preview`; CSV zostaje, gdy było
    wykrycie (albo z `--save-preview`). Bez wykrycia nie zostaje nic.

Ograniczenia: duża ciemna zmiana przy otworze nie oznacza pewnego rozpoznania
wiewiórki (może to być np. ptak lub cień), a bardzo jasna wiewiórka na jasnej
ścianie zostałaby pominięta, bo liczą się tylko obszary ciemniejsze od tła.

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

Domyślnie analiza zatrzymuje się po pierwszym wykryciu (`--mode first`). Aby
przeanalizować cały film i zapisać wszystkie wykryte pojawienia się, użyj
`--mode all`. Próg przekątnej można dostroić opcją `--min-diagonal-px`, a
długość klipu opcją `--clip-seconds`, na przykład:

```bash
./venv_motion/bin/python -m mcrwac.analysis.squirrel_exit_prototype \
  /ścieżka/do/nagrania.mkv --mode all --min-diagonal-px 40 --clip-seconds 120
```

Pełny film z oznaczeniami (do strojenia; ok. 1,4 GB na 25 min) zapisuje
dodatkowo `--save-preview`.

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

Lista przeanalizowanych plików to `analysis/RRRR-MM-DD/wiewiorki/analyzed.txt` (nazwa i
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

Obciążenie (pomiar na VM z 2 rdzeniami, nagranie 30 min, analiza co 4. klatkę,
tryb `all` z 4 wykrytymi zdarzeniami, przy równoległym obciążeniu maszyny):
cały przebieg trwał 17,7 min zegarowo (13,4 min czasu procesora), z czego sama
pętla analizy ok. 5 min, a reszta to cięcie czterech klipów ffmpeg
(przekodowanie H.264). Kolejka z jednym procesem mieści się więc w czasie
nagrywania (nowy plik co 30 min), a w domyślnym trybie `first` powstaje najwyżej
jeden klip na plik.

### Pliki wynikowe

Wszystko trafia domyślnie do podkatalogu z datą nagrania,
`/opt/cameras/recordings/analysis/RRRR-MM-DD/wiewiorki/` (np. `analysis/2026-10-26/`;
tworzony automatycznie), a nazwy mają postać
`<nazwa nagrania>_<...>_RRRRMMDDGGMMSS`:

| Plik | Nazwa | Zawartość |
|------|-------|-----------|
| Podgląd (tylko `--save-preview`) | `<nagranie>_analysis_<czas startu nagrania>.mp4` | cały film: klatki z oznaczonym otworem, obszarem analizy i prostokątami (z przekątną w px); rozdzielczość o połowę mniejsza, kodek mp4v |
| CSV (gdy było wykrycie lub `--save-preview`) | `<nagranie>_analysis_<czas startu nagrania>.csv` | wiersz na klatkę: `czas_s` (czas w nagraniu źródłowym), `x_otworu`, `y_otworu`, `pewnosc_sledzenia`, `zmieniony_obszar`, `najwieksza_przekatna_px`, `duze_obszary`, `zdarzenie` (`wykryto` w klatce potwierdzenia) |
| Klip | `<nagranie>_<czas zdarzenia>.mkv` | fragment źródła od początku zdarzenia (domyślnie 180 s), wideo H.264, dźwięk AAC |

Czas w nazwie (`RRRRMMDDGGMMSS`, np. `20261026133021`) liczony jest z katalogu
daty i godziny w nazwie pliku (`2026-10-26/13-00-08.mkv`); dla klipu dodatkowo
o przesunięcie zdarzenia w filmie. Przykład: zdarzenie 30 s po starcie
`13-00-08.mkv` daje `13-00-08_20261026130038.mkv`. Gdy nazwy nie da się
odczytać, czas startu wynika z daty modyfikacji pliku. Ponowne analizy nie
nadpisują wyników — dostają sufiks, np. `_02`. Opcja `--output` wskazuje inną
bazową ścieżkę (klipy i CSV trafiają wtedy obok niej, z dopisanym czasem).
Podgląd pomija okresy, gdy otworu szukano, więc bywa krótszy od
nagrania; CSV zawsze ma czas źródłowy.
