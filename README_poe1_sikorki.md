# POE1 (sikorki): instrukcja startowa dla nowego agenta

## Jak zacząć (kolejność)

1. **Zdefiniuj cel jednym zdaniem.** Co ma wykryć kamera POE1 (obiekt, zdarzenie, pora dnia) i co ma się stać po wykryciu: klip, powiadomienie ntfy, tylko CSV?
: Jest budka, mniej więcej prostokątna , do znalezienia i trzeba wykrywać małe ptaki które na niej siadają, na powierzchni "prostokąta" ale też i na jego krawędziach. 
Przykładowe obrazki dostaniesz... 
Wynikiem ma być klip od wykrycia ptaszka, długi 1 minuta, csv niech będzie
2. **Zbierz 5–10 próbek.** Pliki z wykrytym zdarzeniem, bez niego i trudne (noc, deszcz, zmiana oświetlenia). Każdy z krótkim opisem, co dzieje się w której sekundzie. To najważniejszy krok, bo bez prawdy odniesienia nie da się stroić ani sprawdzić fałszywych alarmów.
Przykładowe obrazki dostaniesz... pytanie czy musza to być klatki filmu czy wystarczy pełny printscreen - dużo łatwiej dla mnie!
3. **Zbadaj kadr przed pisaniem kodu.** Ruch kamery, rozmiar obiektu, oświetlenie, stałe obszary. Z tego ma wynikać metoda (różnica tła, detekcja ruchu, model), a nie z założeń z POE_BRAMA.
Kamera może się przesunąć, mogę zadbać by była mniej więcej po środku kadru ale powinna być jakaś korekcja gdyz ptaki na niej siadają i ciut przesuwają, minimalnie ale jednak
4. **Zacznij od najprostszego wariantu**, zmierz go na próbkach i dopiero potem komplikuj.
Przygotuję film pokazowy 
5. **Użyj gotowych elementów** z [squirrel_exit_prototype.py](mcrwac/analysis/squirrel_exit_prototype.py): wybór plików po dacie/godzinach (`select_recordings`, `--date`, `--hours`), `--watch` z listą `analyzed.txt`, klipy ffmpeg, `--frame-step`, logowanie `_log`, wzór usługi [squirrel-analysis.service](systemd/squirrel-analysis.service). Nie przenosić lokalizacji otworu (trójkąt komin–szczyt dachu i jego wzorce) ani założeń POE_BRAMA, jeśli kadr jest inny.
6. **Przejrzyj starsze moduły** `adaptive_dark_detector.py`, `blob_detector.py`, `astro_filter.py`, `segment*.py` i `scripts/motion_detect.py`. Mogą już dotyczyć POE1. Ich przydatności nie sprawdzano.

## Dokumentacja do utworzenia (analogicznie do POE_BRAMA)

- `README_POE1_ANALYSIS.md`: algorytm krok po kroku, uruchamianie, `--watch`, pliki wynikowe. Wzór: [README_SQUIRREL_ANALYSIS.md](README_SQUIRREL_ANALYSIS.md).
- `README_POE1_ANALYSIS_TECH.md`: architektura, stałe z uzasadnieniem, formaty plików, ograniczenia, tak aby dało się odtworzyć od zera. Wzór: [README_SQUIRREL_ANALYSIS_TECH.md](README_SQUIRREL_ANALYSIS_TECH.md).
- W [README.md](README.md) tylko krótka sekcja z odnośnikami do tych dwóch plików.
- Wyniki w `/opt/cameras/recordings/analysis/RRRR-MM-DD/`, jak dla POE_BRAMA. Jeśli jedna lista `analyzed.txt` ma obsługiwać obie kamery, trzeba rozdzielić wyniki po nazwie kamery.

## Pierwsza wiadomość do nowego agenta

> Cel POE1: <jedno zdanie>. Próbki: <ścieżki + opis, co w której sekundzie>.
> Przeczytaj README.md, README_SQUIRREL_ANALYSIS.md i README_SQUIRREL_ANALYSIS_TECH.md. Nie zakładaj, że POE1 ma otwór ani wzorce jak POE_BRAMA. Zacznij od zbadania kadru i próbek, potem zaproponuj najprostszą metodę i zmierz ją na próbkach. Dokumentację tworzymy w plikach `README_POE1_ANALYSIS.md` i `README_POE1_ANALYSIS_TECH.md`. Nie commituj bez mojej prośby.
