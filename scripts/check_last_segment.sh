#!/bin/bash

CAMERA="$1"

if [ -z "$CAMERA" ]; then
    echo "Usage: $0 CAMERA_NAME"
    exit 1
fi

EMAIL="konrad.gorski@rcafe.pl"
STATE_FILE="/tmp/camera_${CAMERA}_state"

BASE_DIR="/opt/cameras/recordings/${CAMERA}/$(date +%Y-%m-%d)"

if [ ! -d "$BASE_DIR" ]; then
    exit 0
fi

FILES=($(ls -1 "$BASE_DIR"/*.mkv 2>/dev/null | sort))
COUNT=${#FILES[@]}

if [ "$COUNT" -lt 2 ]; then
    exit 0
fi

FILE="${FILES[$COUNT-2]}"

SEGMENT_TIME=300
MIN_RATIO=0.8
MIN_VIDEO_DURATION=$(echo "$SEGMENT_TIME * $MIN_RATIO" | bc | cut -d'.' -f1)

PREV_STATE="OK"
if [ -f "$STATE_FILE" ]; then
    PREV_STATE=$(cat "$STATE_FILE")
fi

CURRENT_STATE="OK"

# ==============================
# 1️⃣ Czy jest stream video
# ==============================

HAS_VIDEO=$(ffprobe -v error -select_streams v:0 -show_entries stream=index -of csv=p=0 "$FILE")

if [ -z "$HAS_VIDEO" ]; then
    CURRENT_STATE="FAIL_VIDEO"
fi

# ==============================
# 2️⃣ Video duration
# ==============================

if [ "$CURRENT_STATE" = "OK" ]; then

    VIDEO_DURATION=$(ffprobe -v error \
    -select_streams v:0 \
    -show_entries stream_tags=DURATION \
    -of default=noprint_wrappers=1:nokey=1 \
    "$FILE" | head -n1)

    if [ -z "$VIDEO_DURATION" ]; then
        CURRENT_STATE="FAIL_DURATION"
    else
        VIDEO_SECONDS=$(echo "$VIDEO_DURATION" | awk -F: '{ print ($1 * 3600) + ($2 * 60) + $3 }' | cut -d'.' -f1)

        if [ "$VIDEO_SECONDS" -lt "$MIN_VIDEO_DURATION" ]; then
            CURRENT_STATE="FAIL_SHORT"
        fi
    fi
fi

# ==============================
# 3️⃣ Reakcja
# ==============================

if [ "$CURRENT_STATE" != "OK" ]; then

    echo "Camera $CAMERA error: $CURRENT_STATE"

    if [ "$PREV_STATE" = "OK" ]; then
        echo -e "Subject: CAMERA $CAMERA ERROR\n\nFile: $FILE\nReason: $CURRENT_STATE" \
        | msmtp "$EMAIL"
    fi

    echo "$CURRENT_STATE" > "$STATE_FILE"

    sudo systemctl restart camera@"$CAMERA".service

    exit 1
fi

# ==============================
# Jeśli wróciło do normy
# ==============================

if [ "$PREV_STATE" != "OK" ]; then
    echo -e "Subject: CAMERA $CAMERA RECOVERED\n\nRecording returned to normal." \
    | msmtp "$EMAIL"
fi

echo "OK" > "$STATE_FILE"
exit 0