#!/bin/bash

# =========================================
#  cut_mkv.sh
#  Wycinanie fragmentu MKV wg procentów
#  Odporne na locale (zawsze używa kropki)
# =========================================

if [ "$#" -lt 4 ]; then
    echo "Usage:"
    echo "cut_mkv.sh INPUT.mkv START% END% OUTPUT.mkv"
    exit 1
fi

INPUT="$1"
START_PCT="$2"
END_PCT="$3"
OUTPUT="$4"

START_PCT=${START_PCT%\%}
END_PCT=${END_PCT%\%}

if [ ! -f "$INPUT" ]; then
    echo "Input file not found."
    exit 1
fi

# Pobierz długość pliku
DURATION=$(LC_NUMERIC=C ffprobe -v error \
-show_entries format=duration \
-of default=noprint_wrappers=1:nokey=1 \
"$INPUT")

if [ -z "$DURATION" ]; then
    echo "Cannot read duration."
    exit 1
fi

# Obliczenia w sekundach (LC_NUMERIC=C wymusza kropkę)
START_SEC=$(LC_NUMERIC=C awk -v d="$DURATION" -v p="$START_PCT" 'BEGIN { printf "%.3f", d * p / 100 }')
END_SEC=$(LC_NUMERIC=C awk -v d="$DURATION" -v p="$END_PCT" 'BEGIN { printf "%.3f", d * p / 100 }')

echo "File duration: $DURATION s"
echo "Cut from $START_SEC s to $END_SEC s"

# Cięcie szybkie (bez re-encode)
LC_NUMERIC=C ffmpeg -hide_banner -y \
-ss "$START_SEC" \
-i "$INPUT" \
-to "$END_SEC" \
-c copy \
"$OUTPUT"

echo "Done."