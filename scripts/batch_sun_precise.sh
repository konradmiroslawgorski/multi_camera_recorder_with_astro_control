#!/bin/bash

if [ "$#" -lt 1 ]; then
    echo "Użycie:"
    echo "/opt/batch_sun_precise.sh <katalog_dnia> [LEFT TOP WIDTH HEIGHT]"
    exit 1
fi

DAY_DIR="$1"
#DEFAULT_LEFT=500
#DEFAULT_TOP=0
#DEFAULT_WIDTH=500
#DEFAULT_HEIGHT=880

# ROI
LEFT="${2:-0}"
TOP="${3:-0}"
WIDTH="${4:-1400}"
HEIGHT="${5:-880}"

if [ ! -d "$DAY_DIR" ]; then
    echo "Błąd: katalog nie istnieje: $DAY_DIR"
    exit 1
fi

echo "Katalog: $DAY_DIR"

# ---------------- SUN ----------------
LAT=52.2297
LON=21.0122
DATE_STR=$(basename "$DAY_DIR")

SUN_JSON=$(curl -s "https://api.sunrise-sunset.org/json?lat=$LAT&lng=$LON&date=$DATE_STR&formatted=0")

SUNRISE_UTC=$(echo "$SUN_JSON" | jq -r '.results.sunrise')
SUNSET_UTC=$(echo "$SUN_JSON" | jq -r '.results.sunset')

SUNRISE_LOCAL=$(TZ=Europe/Warsaw date -d "$SUNRISE_UTC" +"%H")
SUNSET_LOCAL=$(TZ=Europe/Warsaw date -d "$SUNSET_UTC" +"%H")

START_HOUR=$((10#$SUNRISE_LOCAL - 1))
END_HOUR=$((10#$SUNSET_LOCAL + 1))

[ "$START_HOUR" -lt 0 ] && START_HOUR=0
[ "$END_HOUR" -gt 23 ] && END_HOUR=23

echo "Zakres: ${START_HOUR}:00 - ${END_HOUR}:59"
echo "-----------------------------------------"

# 🔥 DEBUG: czy pliki są widoczne?
echo "Szukam plików: $DAY_DIR/*.mkv"

FOUND=0

for file in "$DAY_DIR"/*.mkv; do

    [ ! -f "$file" ] && continue

    FOUND=1

    echo "Znaleziono: $file"

    [[ "$file" == *_analysis.mp4 ]] && continue

    if sudo lsof "$file" > /dev/null 2>&1; then
        echo "Plik w użyciu — pomijam"
        continue
    fi

    HOUR=$(basename "$file" | cut -d'-' -f1)

    if [ "$HOUR" -lt "$START_HOUR" ] || [ "$HOUR" -gt "$END_HOUR" ]; then
        echo "Poza zakresem → $file"
        #continue
    fi

    echo "Przetwarzam: $file"

    LENGTH=$(ffprobe -v error \
        -show_entries format=duration \
        -of default=noprint_wrappers=1:nokey=1 "$file")

    LENGTH_INT=${LENGTH%.*}

    /opt/extract_glow.sh "$file" 0 $((LENGTH_INT/60)) "$LEFT" "$TOP" "$WIDTH" "$HEIGHT" 0

done

if [ "$FOUND" -eq 0 ]; then
    echo "❌ NIE znaleziono żadnych plików MKV!"
fi

echo "-----------------------------------------"
echo "Batch zakończony."
