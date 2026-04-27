#!/bin/bash

DIR="$1"

if [ -z "$DIR" ]; then
    echo "Podaj katalog z datą np /opt/recordings/live/2026-04-20"
    exit 1
fi

# --- wyciągnij datę z katalogu ---
DATE=$(basename "$DIR")

echo "Data: $DATE"

# --- współrzędne Warszawy ---
LAT=52.2297
LNG=21.0122

# --- pobierz wschód i zachód ---
JSON=$(curl -s "https://api.sunrise-sunset.org/json?lat=$LAT&lng=$LNG&date=$DATE&formatted=0")

SUNRISE_UTC=$(echo "$JSON" | grep sunrise | cut -d '"' -f4)
SUNSET_UTC=$(echo "$JSON" | grep sunset | cut -d '"' -f4)

# --- konwersja do lokalnego czasu ---
SUNRISE_LOCAL=$(date -d "$SUNRISE_UTC" +"%H")
SUNSET_LOCAL=$(date -d "$SUNSET_UTC" +"%H")

echo "Wschód (lokalny): $SUNRISE_LOCAL:00"
echo "Zachód (lokalny): $SUNSET_LOCAL:00"

# --- budujemy dynamiczne okna ---
MORNING_START=$((SUNRISE_LOCAL - 1))
MORNING_END=$((SUNRISE_LOCAL + 4))

EVENING_START=$((SUNSET_LOCAL - 2))
EVENING_END=$((SUNSET_LOCAL + 1))

echo "Okno poranne: $MORNING_START-$MORNING_END"
echo "Okno wieczorne: $EVENING_START-$EVENING_END"

process_file() {
    ./extract_idx_glow.sh "$1" 0 3 400 0 600 400
}

for file in "$DIR"/*.mp4; do

    base=$(basename "$file")
    hour=${base:0:2}

    if (( hour >= MORNING_START && hour < MORNING_END )); then
        echo "Poranne dynamiczne: $file"
        process_file "$file"
    fi

    if (( hour >= EVENING_START && hour < EVENING_END )); then
        echo "Wieczorne dynamiczne: $file"
        process_file "$file"
    fi

done

echo "Gotowe."
