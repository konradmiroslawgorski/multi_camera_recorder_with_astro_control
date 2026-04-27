#!/bin/bash

source /opt/cameras.conf
source /opt/lib_astro.sh
if [ -z "$BASE_DIR" ] || [ -z "$LOG_DIR" ]; then
    echo "ERROR: BASE_DIR or LOG_DIR not set"
    exit 1
fi

declare -A PIDS

start_camera() {

    CAM="$1"

    RTSP_VAR="CAM_${CAM}_RTSP"
    RTSP="${!RTSP_VAR}"

    SEG_VAR="CAM_${CAM}_SEGMENT_TIME"
    SEG="${!SEG_VAR}"

    TODAY=$(date +%F)

    mkdir -p "${BASE_DIR}/${CAM}/${TODAY}"
    echo "start_camera: mkdir" >> "${LOG_DIR}/${CAM}.log"
    echo "${BASE_DIR}/${CAM}/${TODAY}" >> "${LOG_DIR}/${CAM}.log"

    echo "$(date '+%F %T') START $CAM" >> "${LOG_DIR}/${CAM}.log"

    /usr/bin/ffmpeg \
        -hide_banner \
        -loglevel error \
        -rtsp_transport tcp \
	-stimeout 5000000 \
        -i "$RTSP" \
        -c copy \
        -f segment \
        -segment_time "$SEG" \
        -reset_timestamps 1 \
        -strftime 1 \
        "${BASE_DIR}/${CAM}/%Y-%m-%d/%H-%M-%S.mkv" \
        >> "${LOG_DIR}/${CAM}.log" 2>&1 &

    PIDS[$CAM]=$!
}
stop_camera() {

    CAM="$1"

    if [ -n "${PIDS[$CAM]}" ]; then
        echo "$(date) STOP $CAM"
	kill -TERM "${PIDS[$CAM]}"
	wait "${PIDS[$CAM]}" 2>/dev/null
        unset PIDS[$CAM]
    fi
}

is_camera_running() {
    CAM="$1"
    PID="${PIDS[$CAM]}"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

# ======================================
# GŁÓWNA PĘTLA
# ======================================

while true; do
    CURRENT_DATE=$(date +%F)

    for CAM in "${CAMERAS[@]}"; do
	echo "główna pętla: mkdir" >> "${LOG_DIR}/${CAM}.log"
   	echo "${BASE_DIR}/${CAM}/${CURRENT_DATE}" >> "${LOG_DIR}/${CAM}.log"

        mkdir -p "${BASE_DIR}/${CAM}/${CURRENT_DATE}"
    done
    for CAM in "${CAMERAS[@]}"; do

        ENABLED_VAR="CAM_${CAM}_ENABLED"
        [ "${!ENABLED_VAR}" != "1" ] && continue

        MODE_VAR="CAM_${CAM}_RECORD_MODE"
        MODE="${!MODE_VAR}"

        should_record=0

        if [ "$MODE" = "always" ]; then
            should_record=1
        elif [ "$MODE" = "astro" ]; then
            if is_in_astro_window; then
                should_record=1
            fi
        fi
	
        if [ "$should_record" = "1" ]; then
            if ! is_camera_running "$CAM"; then
                start_camera "$CAM"
            fi
        else
            if is_camera_running "$CAM"; then
                stop_camera "$CAM"
            fi
        fi

    done

    sleep 30

done
