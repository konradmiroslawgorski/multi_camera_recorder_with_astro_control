# Multi Camera Recorder with Astro Control

Repozytorium: https://github.com/konradmiroslawgorski/multi_camera_recorder_with_astro_control

## Środowisko uruchomieniowe (VM)

Rejestrator działa na maszynie wirtualnej z Ubuntu. Zanim zaczniesz pracę
(wdrożenie, restart usług, podgląd logów), **uruchom VM serwera**:

- adres IP: `192.168.196.10`
- użytkownik: ubuntu, hasło: kcb
- alias SSH: `ubuntu-mv` (wpis w lokalnym pliku `.ssh/config` na Windowsie,
  zwykle `C:\Users\<użytkownik>\.ssh\config`)

Połączenie: `ssh ubuntu-mv`. Ten sam alias wybierzesz w VS Code (Remote - SSH).
Gdy VM jest wyłączona, kamery nie są nagrywane i nie przychodzą powiadomienia.

## Astronomiczne okno nagrywania

Usługi kamer korzystają z `config/recording_config.yaml`, aby nagrywać od
ustawionego czasu przed wschodem słońca do ustawionego czasu po zachodzie.
Godziny wschodu i zachodu słońca są obliczane lokalnie przez bibliotekę Astral
dla skonfigurowanej lokalizacji i strefy czasowej; rejestrator nie wykonuje
żądań HTTP.

Domyślne okno obejmuje czas od godziny przed wschodem słońca do godziny po
zachodzie. Aby je zmienić, edytuj `recording_window.start` i
`recording_window.stop` w pliku YAML. Podczas oczekiwania rejestrator sprawdza
lokalny harmonogram co 30 sekund. Jeśli usługa zostanie uruchomiona w trakcie
okna, rozpoczyna pracę FFmpeg. Na końcu okna wysyła do FFmpeg sygnał SIGINT,
aby umożliwić mu zapisanie bieżącego segmentu.

Zainstaluj wymagane zależności Pythona w środowisku `venv_motion` poleceniem
`venv_motion/bin/pip install -r requirements.txt`. Po zmianie konfiguracji
zrestartuj usługi kamer, na przykład:
`sudo systemctl restart camera@POE1 camera@POE_BRAMA`.

## Eksperymentalna analiza wyjścia wiewiórki

Prototyp w `mcrwac/analysis/squirrel_exit_prototype.py` offline'owo analizuje
nagrania kamery `POE_BRAMA` i wykrywa moment, gdy z małego otworu w ścianie
wygląda wiewiórka. Nie wysyła alarmów i nie jest podłączony do usług
nagrywających. Wiewiórka nie jest rozpoznawana jako gatunek: program wykrywa
**dużą, ciemną zmianę obrazu tuż przy otworze**.

### Algorytm krok po kroku

Poniższe wartości (piksele dla nagrań 2304×1296, 12 kl./s) są stałymi na górze
pliku prototypu.

1. **Szukanie otworu.** Najpierw z klatek próbkowanych co 0,25 s przez 3 s
   (13 klatek) program wyznacza położenie otworu *metodą wzorca*: dopasowuje
   cechy SIFT dachu i ścian (kominy, antena, krawędzie) do wzorców w
   [`mcrwac/analysis/reference/`](mcrwac/analysis/reference/): `poe_brama_roof.jpg`
   (pochmurny poranek), `poe_brama_roof_sun.jpg` (pełne słońce) i
   `poe_brama_roof_blue_sky.jpg` (słońce i błękitne niebo); dla każdej
   klatki próbowane są kolejno wszystkie wzorce, bo przy innym oświetleniu
   jeden z nich może się nie dopasować
   (RANSAC, przynajmniej 12 zgodnych punktów, skala 0,85–1,18, obrót do 15°)
   i przelicza znane położenie otworu na bieżący obraz. Dzięki temu działa także
   po restarcie lub przesunięciu kamery. Wynik jest przyjęty, gdy zgadza się w
   co najmniej połowie klatek (rozrzut do 12 px). Aby dodać wzorzec dla nowego
   oświetlenia lub ustawienia kamery, zapisz fragment klatki (szary JPG) w tym
   katalogu i dopisz go z początkiem kadru oraz położeniem otworu do
   `REFERENCES` w pliku prototypu. Uwaga: wzorce są dopasowane do widoku
   kamery `POE_BRAMA`.
2. **Plan B: szukanie samego otworu.** Jeśli wzorzec się nie dopasuje, program
   szuka w obrazie małych, ciemnych plamek (jasność < 45, pole 15–100 px,
   proporcje 1,2–4:1), punktując je krawędziami okna/ściany po obu stronach i
   ciemnością. Kandydat musi się powtarzać w ≥ 60% klatek, a jego wynik
   przewyższać konkurenta o ≥ 18 punktów; w przeciwnym razie lokalizacja jest
   „niejednoznaczna".
3. **Otwór niewidoczny — szukanie siatką i bisekcją.** Przy złym świetle,
   deszczu lub ciemnej chmurze program nie kończy się błędem. Zamiast
   sprawdzać film co 15 s (jedna próba na 13 klatkach trwa ok. minuty),
   sprawdza **jedną klatkę co 60 s** (dopasowanie do wzorców, kilka sekund).
   Po pierwszej udanej klatce wykonuje **bisekcję** między nią a ostatnią
   klatką bez otworu, do dokładności 15 s, i potwierdza wynik krokiem 1 (13
   klatek). Analiza rusza od tego momentu, więc nie traci początku. Jeśli
   wzorce nie działają w żadnym punkcie, na rzadkiej siatce (co 5 min) próbuje
   planu B. Gdy nie znajdzie otworu w całym pozostałym fragmencie, kończy plik
   bez wyników. Założenie: otwór jest widoczny od jednego momentu w danym
   przedziale 60 s; krótsze okna widoczności (np. chmura mijająca w mniej niż
   minutę) mogą zostać pominięte.
4. **Śledzenie.** Dla każdej analizowanej klatki program dopasowuje wzorzec otoczenia otworu
   (`matchTemplate`, okno ±48 px, szukanie ±32 px) i koryguje położenie, więc
   drobne ruchy kamery nie przeszkadzają. Klatki o pewności < 0,60 są pomijane.
   Po ok. 15 s takich klatek otwór uznaje się za utracony i program wraca do
   kroku 1, szukając tylko w promieniu 100 px od ostatniej pozycji (próg
   stabilności obniżony do 40%), aż otwór pojawi się znowu.
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

Lista przeanalizowanych plików to `analysis/RRRR-MM-DD/analyzed.txt` (nazwa i
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
`/opt/cameras/recordings/analysis/RRRR-MM-DD/` (np. `analysis/2026-10-26/`;
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

## Powiadomienia push (ntfy)

Rejestrator wysyła powiadomienia przez [ntfy](https://ntfy.sh) (push na iPhone
przez aplikację ntfy), bez dodatkowych zależności. Format:
`data czas kamera opis`, np. `2026-10-23 13:06:40 POE1 start nagrywania filmu [10-00-00.mkv]`.

Zdarzenia:
- **start / stop nagrywania** z nazwą pliku `.mkv` (pierwszy plik po starcie, ostatni przy zatrzymaniu),
- **status** (konfiguracja + dane astro: wschód, zachód, okno, z adnotacją „astro OK"):
  tylko rano po starcie nagrywania otwartym przez astro i wieczorem po zatrzymaniu
  na końcu okna. Restart usługi lub nagrywania w ciągu dnia nie wysyła statusu,
- **błędy** (nieoczekiwane wyjście FFmpeg, brak nowego pliku): pierwszy od razu,
  kolejne w ciągu godziny (`error_group_minutes`) w jednym zbiorczym powiadomieniu.

Konfiguracja: sekcja `notifications` w `config/recording_config.yaml`. Nazwa tematu
działa jak hasło (publiczny serwer), więc nie trzymamy jej w repozytorium:

1. `cp config/notify.env.example config/notify.env` i ustaw losowy `NTFY_TOPIC`
   (plik jest w `.gitignore`; można też użyć zmiennej środowiskowej).
2. W aplikacji ntfy na iPhone zasubskrybuj ten sam temat.
3. `sudo systemctl restart camera@POE1 camera@POE_BRAMA`.

Bez `NTFY_TOPIC` powiadomienia są wyłączone (ostrzeżenie w logu), a nagrywanie działa normalnie.
Awaria sieci nigdy nie blokuje nagrywania.

## Dostęp do nagrań (sieć lokalna)

VM ma adres `192.168.196.10` (VMware NAT), dostępny z laptopa z Windowsem.
Zdalny dostęp spoza domu nie jest skonfigurowany. **Nie wystawiamy SSH ani SMB do internetu.**

Struktura plików: `/opt/cameras/recordings/<KAMERA>/<RRRR-MM-DD>/<GG-MM-SS>.mkv`.

### SMB (tylko do odczytu)

Udział `cameras` jest zdefiniowany w `/etc/samba/smb.conf`
(`path = /opt/cameras`, `read only = yes`, `valid users = ubuntu`).
Hasło Samby jest osobne od hasła systemowego: `sudo smbpasswd -a ubuntu`.

- Windows: `\\192.168.196.10\cameras` (login `ubuntu`).
- VLC (Windows / iPhone w tej samej sieci): SMB, serwer `192.168.196.10`,
  udział `cameras`, użytkownik `ubuntu`. Dalej: `recordings` → kamera → data → plik `.mkv`.

SFTP w VLC na iOS wracał do menu bez błędu, więc nie jest zalecany.

### Opcjonalnie: HTTP tylko do odczytu (nginx)

Nagrania na porcie `8080`, bez możliwości zapisu, dostępne z LAN VM:

```bash
sudo ln -s /home/ubuntu/projects/mcrwac/config/nginx/recordings.conf /etc/nginx/conf.d/recordings.conf
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1:8080/recordings/ | head -1    # oczekiwane: HTTP/1.1 200 OK
```

Lista plików: `http://192.168.196.10:8080/recordings/`. W VLC: **Sieć** →
**Otwórz strumień sieciowy** i adres pliku, np.
`http://192.168.196.10:8080/recordings/POE1/2026-10-23/13-35-03.mkv`.

### Diagnostyka

| Objaw | Sprawdź |
|---|---|
| SMB „access denied” | złe hasło Samby, ustaw nowe: `sudo smbpasswd -a ubuntu` |
| Brak plików | czy VM jest uruchomiona i usługi `camera@...` działają |
