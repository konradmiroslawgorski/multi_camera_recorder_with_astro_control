# README_2 — część techniczna: analiza wyjścia wiewiórki

Uzupełnienie [README.md](README.md). Opisuje architekturę, stałe z uzasadnieniem, procedurę tworzenia wzorców, formaty plików i ograniczenia, tak aby rozwiązanie dało się odtworzyć od zera.

## 1. Cel i założenia

- Kamera POE_BRAMA (2304×1296, 12 kl./s) nagrywa ścianę z małym otworem (ok. 10×15 px). Wiewiórka wychodzi z otworu.
- Analiza jest **offline** (po nagraniu), nie wpływa na rejestrator. Wynik: klipy 3 min od wykrycia, CSV, lista przeanalizowanych plików.
- Wymagania: Python 3.10, `numpy`, `opencv-python` (z SIFT), `ffmpeg` w PATH (klipy), `pip install -r requirements.txt` do `venv_motion`.
- Nagrania: `/opt/cameras/recordings/<KAMERA>/RRRR-MM-DD/GG-MM-SS.mkv` (czas startu w nazwie, segmenty ok. 30 min). Wyniki: `/opt/cameras/recordings/analysis/RRRR-MM-DD/`.
- Kamera po restarcie zmienia położenie (25.10 otwór ≈ (999, 793); 26–27.10 ≈ (347, 744)), a światło zmienia się od pochmurnego po pełne słońce i błękitne niebo. Stąd lokalizacja otworu na każdym pliku, nie stałe współrzędne.

## 2. Architektura (`mcrwac/analysis/squirrel_exit_prototype.py`)

Jeden moduł, bez stanu globalnego poza `lru_cache` wzorców.

| Etap | Funkcje |
|---|---|
| Wzorce | `REFERENCES` (`ReferenceSpec(file, crop_origin, opening)`), `_load_references` (SIFT, cache) |
| Lokalizacja otworu | `_probe_reference` (1 klatka), `locate_opening_by_reference` (13 klatek), `_find_opening` (siatka + bisekcja + plan B), `locate_opening`/`find_opening_candidates` (plan B) |
| Śledzenie | `track_opening` (matchTemplate, okno ±32 px) |
| Wykrywanie zmian | `AdaptiveBackground`, `_update_background`, `_activity_mask`, `activity_regions` |
| Decyzja | `AppearanceDetector` (okno i liczba trafień) |
| Wyniki | `_save_appearance_clips` (ffmpeg), CSV, `analyzed.txt` |
| Sterowanie | `analyze_video`, `select_recordings`, `next_pending_recording`, `watch_recordings`, `main` |
| Log | `_log` — `[hh:mm:ss]` zegarowy + `[GG-MM-SS-mmm]` czas w nagraniu |

Przepływ `analyze_video`: odczyt metadanych → `_find_opening` → pętla klatek (co `--frame-step`-ta, reszta `grab()`) → `track_opening` → `_activity_mask` → `activity_regions` → `AppearanceDetector` → przy wykryciu zapis klipu (tryb `first` kończy, `all` kontynuuje po zwolnieniu) → jeśli otwór zniknie na `LOST_TRACKING_SECONDS`, ponowne `_find_opening` od bieżącej pozycji.

## 3. Lokalizacja otworu

1. **Wzorce SIFT.** Każdy wzorzec to wycinek pierwszej klatki z danego oświetlenia (`reference/*.jpg`), z `crop_origin` (lewy górny róg wycinka w pełnej klatce) i `opening` (współrzędne otworu w pełnej klatce wzorca). Obecne: pochmurno (`poe_brama_roof.jpg`), słońce (`…_sun.jpg`), błękitne niebo (`…_blue_sky.jpg`, crop `[450:1050, 0:850]`, origin (0, 450), otwór (347, 744)).
2. Dopasowanie: SIFT + `estimateAffinePartial2D` (RANSAC, próg 4 px). Akceptacja: ≥ 12 inlierów, skala 0,85–1,18, obrót ≤ 15°. Pozycja otworu = transformacja punktu `opening`.
3. **Pełne potwierdzenie**: 13 klatek, zgodność ≥ połowy, rozrzut ≤ 12 px. Szybki test (`min_estimates=1`) używa jednej klatki.
4. **`_find_opening`**: klatka 0 → jeśli OK, potwierdzenie i analiza od razu. Jeśli nie: siatka co 60 s (1 klatka na punkt), po pierwszym trafieniu bisekcja wstecz do dokładności 15 s, potem potwierdzenie. Gdy siatka nic nie da — **plan B** co 300 s (wykrywanie ciemnej plamy: próg < 45, pole 15–100 px, proporcje 1,2–4, wynik krawędziowy ≥ 110, przewaga ≥ 18 nad drugim kandydatem). Wyjście z pliku dopiero, gdy w całym pozostałym fragmencie brak otworu.
5. Koszt: dopasowanie jednej klatki ok. 4–5 s, 13 klatek 40–45 s — dlatego siatka zamiast gęstego próbkowania (pierwsza wersja z próbą co 15 s była za wolna).

### Jak dodać wzorzec (nowe światło / pozycja kamery)
1. Wyciągnij klatkę, na której otwór jest widoczny, np. `ffmpeg -ss 0 -i plik.mkv -frames:v 1 f.png`.
2. Wytnij obszar z dachem/ścianą wokół otworu (ok. 600×850 px) z dużą ilością charakterystycznych krawędzi; zapisz jako JPG w `mcrwac/analysis/reference/`.
3. Zanotuj `crop_origin = (x0, y0)` wycinka i `opening = (x, y)` otworu w pełnej klatce (z klatki, nie z wycinka).
4. Dopisz `ReferenceSpec` do `REFERENCES`.
5. Sprawdź na kilku momentach każdego pliku testowego (0/60/300/900/1700 s), czy zwraca tę samą pozycję. Powód dopisania trzeciego wzorca: 09-22-43 dawał tylko 4–8 inlierów przy wymaganych 12.

## 4. Wykrywanie wiewiórki

- **Śledzenie**: szablon 96×96 px (`TEMPLATE_RADIUS = 48`) z klatki wzorca, `TM_CCOEFF_NORMED`, szukanie ±32 px; pewność < 0,6 → klatka pomijana; brak otworu przez 15 s → ponowne szukanie.
- **ROI**: ±45 px poziomo, 60 px w górę, 80 px w dół od otworu.
- **Tło adaptacyjne**: start z wzorca; `accumulateWeighted` α = 0,01; piksele zmienione są zamrożone do 30 s, dłuższa zmiana (światło) wchodzi do tła.
- **Maska**: od różnicy odjęta mediana (globalna ekspozycja); piksel aktywny, gdy `tło − obraz > 25` **i** `obraz < tło × 0,65`; rozmycie 3×3, otwarcie morfologiczne 3×3.
- **Regiony**: przekątna > 30 px, krótszy bok ≥ 12 px, proporcje ≤ 6.
- **Decyzja**: przy `--frame-step 1` okno 5 klatek / 3 trafienia; przy kroku > 1 okno 3 / 2 trafienia; zwolnienie po przerwie 1,5 s.
- Domyślnie `--frame-step 4` (≈ 3 analizowane kl./s): szybciej (klip 60 s: 42 s zamiast 60 s) i nadal łapie wrony; kosztem przelotów < ok. 0,7 s i zdarzeń granicznych.

## 5. Uzasadnienia i historia strojenia

| Stała | Wartość | Dlaczego |
|---|---|---|
| `MIN_DARKENING_RATIO` | 0,65 | Fałszywy alarm w 14-30-24 (zmiana tła/cień od słońca): obraz tylko nieco ciemniejszy. Prawdziwa wiewiórka: region ok. 15×27–16×31 px, 95–99% pikseli < 60. Dla 0,6 wykrycie spóźnione (51,2 s zamiast 45,7 s); 0,7 daje 0 fałszywych; 0,75 → 81, 0,8 → 216 fałszywych regionów |
| `DEFAULT_MIN_DIAGONAL_PX` | 30 | Odsiewa szum i drobne cienie; prawdziwa wiewiórka ma ok. 31–35 px |
| `DEFAULT_CLIP_SECONDS` | 180 | 60 s było za mało |
| `SEARCH_GRID_SECONDS` | 60 | Kompromis szybkość/ryzyko pominięcia okien widoczności < 60 s |
| `SEARCH_RETRY_SECONDS` | 15 | Dokładność bisekcji |
| `SEARCH_FALLBACK_SECONDS` | 300 | Plan B jest tani, ale mniej wiarygodny |
| `WATCH_SETTLE_SECONDS` | 120 | Plik uznany za zakończony, gdy brak zapisu 120 s lub jest nowszy segment |

Pomiary (VM, 2 rdzenie, obciążona): odczyt sekwencyjny 70 kl./s; plik 30 min — pętla analizy ok. 5 min, reszta to ffmpeg klipów; pełny przebieg z 4 klipami 17,7 min (1065 s zegarowo, 760 s CPU).

Pliki kontrolne: `2026-10-25/05-49-02-18-19.mkv` (klip, wiewiórka ok. 45,7 s), `13-00-08.mkv` (wiewiórka wraca do dziury — wykrywana), `2026-10-26/14-30-24.mkv` (słońce, brak wiewiórki — nie może być wykrycia), `2026-10-27/09-22-43.mkv` (jasno, błękitne niebo — otwór (347, 744)).

## 6. Formaty plików

- Klip: `<stem>_<RRRRMMDDGGMMSS>.mkv` (czas wykrycia); przy kolizji sufiks `_02`, `_03`… Klip tworzony `ffmpeg` (libx264 veryfast, crf 20, aac), start = klatka wykrycia, długość ≤ `--clip-seconds`.
- CSV (tylko przy wykryciu lub `--save-preview`): zdarzenia z czasem w nagraniu; podgląd MP4 tylko z `--save-preview`.
- `analyzed.txt` w katalogu dnia: linie `nazwa<TAB>ok|blad`. Plik bez otworu też jest `ok`. Przerwana analiza kasuje częściowe CSV/MP4 i nie wpisuje do listy.

## 7. Tryb `--watch` i usługa

`next_pending_recording` wybiera najstarszy nieprzeanalizowany plik z dnia startu i kolejnych (nie starsze; do tego `--date`). `watch_recordings` odpytuje co `--poll-seconds` (30), uruchamia analizę z `nice` 10; SIGTERM (`_terminate`) kończy czysto. `--hours` działa też z `--watch`. Usługa: `systemd/squirrel-analysis.service` (Nice=10, IOSchedulingClass=idle), kopiowana do `/etc/systemd/system/`, `systemctl enable --now squirrel-analysis`.

## 8. Testy

`./venv_motion/bin/python -m unittest tests.test_squirrel_exit_prototype tests.test_notifier tests.test_time_window` — 26 testów analizy (lokalizacja, śledzenie, filtry, detektor, ścieżki, `_find_opening`, tło, `select_recordings`, watch).

## 9. Znane ograniczenia i pomysły

- Plan B może zaakceptować fałszywy otwór, gdy żaden wzorzec nie pasuje — przy nowym oświetleniu/pozycji dopisz wzorzec.
- Okna widoczności otworu krótsze niż 60 s mogą zostać pominięte; bardzo jasna wiewiórka na jasnym tle byłaby pominięta.
- Krok 4 może gubić przeloty < 0,7 s; `--frame-step 1` łapie więcej zdarzeń granicznych.
- `--watch` nie obejmuje starszych dni; pomysł: `--since RRRR-MM-DD`.
- Pomysły: powiadomienie ntfy po wykryciu, automatyczne tworzenie wzorców, wnioskowanie o oświetleniu zamiast stałej 0,65.
