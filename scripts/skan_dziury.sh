#!/bin/bash

set -e

# =========================

# KONFIG

# =========================

DIR="/opt/cameras/recordings/POE_BRAMA/2026-04-28"
ASTRO="/home/ubuntu/projects/mcrwac/config/astro_today.json"

CROP_W=75
CROP_H=145
CROP_X=1115
CROP_Y=1903

OUT_DIR="$DIR/godzinowe"
TMP_LIST="$DIR/tmp_list.txt"
LISTA="$DIR/lista_dzien.txt"
LISTA_TEST="$DIR/lista_test.txt"

mkdir -p "$OUT_DIR"

# =========================

# CZYSZCZENIE

# =========================

rm -f "$TMP_LIST"
: > "$LISTA"
: > "$LISTA_TEST"

# =========================

# ASTRO

# =========================

echo "=== Wczytuję astro ==="

SUNRISE=$(jq -r '.sunrise' "$ASTRO" | tr ':' '-')
SUNSET=$(jq -r '.sunset' "$ASTRO" | tr ':' '-')

echo "Sunrise: $SUNRISE"
echo "Sunset : $SUNSET"

cd "$DIR"

# =========================

# WYBÓR PLIKÓW

# =========================

echo "=== Wybór plików dziennych ==="

for f in *.mkv; do
[[ ! "$f" =~ ^[0-2][0-9]-[0-5][0-9]-[0-5][0-9].mkv$ ]] && continue

t="${f%.mkv}"

if [[ "$t" > "$SUNRISE" && "$t" < "$SUNSET" ]]; then
echo "$f" >> "$LISTA"
fi
done

echo "=== Lista (pierwsze 3 pliki TEST) ==="
head -n 3 "$LISTA" > "$LISTA_TEST"
cat "$LISTA_TEST"

# =========================

# CROP

# =========================

echo "=== Crop ==="

while IFS= read -r f; do
[ -z "$f" ] && continue
[ ! -f "$f" ] && echo "Brak pliku: $f" && continue

out="${f%.mkv}_DZIURA.mkv"

if [ -f "$out" ]; then
echo "Pomijam crop (już istnieje): $out"
continue
fi

echo "Przetwarzam: $f -> $out"

ffmpeg -i "$f" -vf "crop=${CROP_W}:${CROP_H}:${CROP_X}:${CROP_Y}" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "$out"

done < "$LISTA_TEST"

# =========================

# ŁĄCZENIE

# =========================

echo "=== Łączenie ==="

OUT_FILE="$OUT_DIR/test_1h.mkv"

if [ -f "$OUT_FILE" ]; then
echo "Pomijam łączenie (już istnieje): $OUT_FILE"
echo "=== DONE ==="
exit 0
fi

rm -f "$TMP_LIST"

while IFS= read -r f; do
[ -z "$f" ] && continue

out="${f%.mkv}_DZIURA.mkv"

if [ -f "$out" ]; then
echo "file '$PWD/$out'" >> "$TMP_LIST"
else
echo "Pomijam brakujący: $out"
fi

done < "$LISTA_TEST"

# sprawdź ile plików mamy

COUNT=$(wc -l < "$TMP_LIST")

if [ "$COUNT" -lt 2 ]; then
echo "Za mało plików do łączenia ($COUNT)"
echo "=== DONE ==="
exit 0
fi

echo "Tworzę plik wynikowy ($COUNT plików):"

ffmpeg -f concat -safe 0 -i "$TMP_LIST" -c copy "$OUT_FILE"

echo "=== DONE ==="
echo "Wynik: $OUT_FILE"
