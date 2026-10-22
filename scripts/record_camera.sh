#!/bin/bash

# Wczytane z EnvironmentFile:
# RTSP_URL
# SEGMENT_TIME
# PRE_CROP
# CAMERA

BASE_DIR="/opt/cameras/recordings/${CAMERA}"

echo "Recorder started for $CAMERA"

while true; do

    CURRENT_DATE=$(date +%Y-%m-%d)
    OUTPUT_DIR="${BASE_DIR}/${CURRENT_DATE}"

    mkdir -p "$OUTPUT_DIR"

    if [ "$PRE_CROP" = "lower_half" ]; then
        echo "Recording with PRE_CROP lower_half into $OUTPUT_DIR"

        /usr/bin/ffmpeg \
            -hide_banner \
            -loglevel warning \
            -fflags +genpts \
            -rtsp_transport tcp \
            -stimeout 5000000 \
            -i "$RTSP_URL" \
            -vf "crop=iw:ih/2:0:ih/2" \
            -c:v libx264 \
            -preset veryfast \
            -crf 18 \
            -c:a copy \
            -f segment \
            -segment_time "$SEGMENT_TIME" \
            -reset_timestamps 1 \
            -strftime 1 \
            "${BASE_DIR}/%Y-%m-%d/%H-%M-%S.mkv"

    else
        echo "Recording without PRE_CROP into $OUTPUT_DIR"

        /usr/bin/ffmpeg \
            -hide_banner \
            -loglevel warning \
            -fflags +genpts \
            -rtsp_transport tcp \
            -stimeout 5000000 \
            -i "$RTSP_URL" \
            -c copy \
            -f segment \
            -segment_time "$SEGMENT_TIME" \
            -reset_timestamps 1 \
            -strftime 1 \
            "${BASE_DIR}/%Y-%m-%d/%H-%M-%S.mkv"
    fi

    echo "FFmpeg exited for $CAMERA. Restarting in 5 seconds..."
    sleep 5

done