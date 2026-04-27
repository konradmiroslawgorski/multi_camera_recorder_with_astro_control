#!/bin/bash

INPUT="$1"
PERCENT="$2"
MINUTES="$3"
LEFT="$4"
TOP="$5"
WIDTH="$6"
HEIGHT="$7"
START_OVERRIDE="$8"
OUTPUT_OVERRIDE="$9"

if [ ! -f "$INPUT" ]; then
    echo "BŁĄD: Plik nie istnieje: $INPUT"
    exit 1
fi

# -------------------------------------------------
# 🔒 Bezpieczny OUTPUT (zawsze w katalogu źródła)
# -------------------------------------------------
DIR=$(dirname "$INPUT")
NAME=$(basename "$INPUT" .mkv)

if [ -n "$OUTPUT_OVERRIDE" ]; then
    OUTPUT="$OUTPUT_OVERRIDE"
else
    OUTPUT="$DIR/${NAME}_analysis.mp4"
fi

if [ -f "$OUTPUT" ]; then
    echo "Już istnieje → pomijam $OUTPUT"
    exit 0
fi

# -------------------------------------------------
# ⏱️ START TIME
# -------------------------------------------------
BASENAME=$(basename "$INPUT")

START_H=$(echo "$BASENAME" | cut -d'-' -f1)
START_M=$(echo "$BASENAME" | cut -d'-' -f2)
START_S=$(echo "$BASENAME" | cut -d'-' -f3 | cut -d'.' -f1)

START_TOTAL=$((10#$START_H*3600 + 10#$START_M*60 + 10#$START_S))

DUR=$(ffprobe -v error -show_entries format=duration \
-of default=noprint_wrappers=1:nokey=1 "$INPUT")

if [ -n "$START_OVERRIDE" ]; then
    START="$START_OVERRIDE"
else
    START=$(echo "$DUR * $PERCENT / 100" | bc -l)
fi

LENGTH=$(echo "$MINUTES * 60" | bc)

echo "Start: $START s"
echo "Długość: $LENGTH s"
echo "Tworzę: $OUTPUT"

# -------------------------------------------------
# 🎬 FILTER
# -------------------------------------------------
FILTER="crop=${WIDTH}:${HEIGHT}:${LEFT}:${TOP},tmix=frames=4:weights='1 1 1 1',setpts=PTS/4,fps=25,drawtext=fontcolor=white:fontsize=28:box=1:boxcolor=black@0.5:text='%{eif\\:(t*4+${START_TOTAL})/3600\\:d\\:2}\\:%{eif\\:mod((t*4+${START_TOTAL})/60\\,60)\\:d\\:2}\\:%{eif\\:mod(t*4+${START_TOTAL}\\,60)\\:d\\:2}':x=w-tw-30:y=h-th-50"
# -------------------------------------------------
# 🚀 FFMPEG (KLUCZOWE ZMIANY)
# -------------------------------------------------
ffmpeg -loglevel error -y \
-ss "$START" -i "$INPUT" \
-t "$LENGTH" \
-vf "$FILTER" \
-c:v libx264 \
-preset veryfast \
-crf 23 \
-g 50 \
-keyint_min 50 \
-sc_threshold 0 \
-tune fastdecode \
-maxrate 4M \
-bufsize 8M \
-pix_fmt yuv420p \
-an \
"$OUTPUT"
echo "Gotowe."
