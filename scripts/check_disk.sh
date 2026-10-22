#!/bin/bash

# ===== CONFIG =====
CHECK_PATH="/opt"
RECORDINGS_PATH="/opt/cameras/recordings"
LOGFILE="/home/ubuntu/disk_monitor.log"
STATEFILE="/home/ubuntu/.disk_state"
EMAIL="konrad.gorski@rcafe.pl"

THRESHOLD_WARNING=80
THRESHOLD_PRUNE=82
THRESHOLD_CRITICAL=88
TARGET_AFTER_PRUNE=78

# ===== FUNCTIONS =====

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOGFILE"
}

get_usage() {
    df "$CHECK_PATH" | awk 'NR==2 {gsub("%",""); print $5}'
}

send_mail_once() {
    local subject="$1"
    local body="$2"

    if [ -f "$STATEFILE" ] && grep -q "$subject" "$STATEFILE"; then
        return
    fi

    echo "$body" | msmtp "$EMAIL" 2>/dev/null
    echo "$subject" > "$STATEFILE"
}

clear_mail_state() {
    rm -f "$STATEFILE"
}

prune_oldest_day() {
    oldest=$(find "$RECORDINGS_PATH" -mindepth 2 -maxdepth 2 -type d | sort | head -n 1)

    if [ -z "$oldest" ]; then
        return 1
    fi

    log "[PRUNE] Removing $oldest"
    rm -rf "$oldest"
    return 0
}

# ===== MAIN =====

USAGE=$(get_usage)
log "[STATUS] Disk usage: ${USAGE}%"

if [ "$USAGE" -ge "$THRESHOLD_PRUNE" ]; then
    log "[PRUNE] Starting cleanup loop"

    while [ "$USAGE" -ge "$TARGET_AFTER_PRUNE" ]; do
        prune_oldest_day || break
        USAGE=$(get_usage)
        log "[PRUNE] Usage after delete: ${USAGE}%"
    done
fi

USAGE=$(get_usage)

if [ "$USAGE" -ge "$THRESHOLD_CRITICAL" ]; then
    log "[CRITICAL] Disk usage >= ${THRESHOLD_CRITICAL}%"

    send_mail_once "CRITICAL" "Disk usage critical: ${USAGE}%"

    log "[STOP] camera@POE1"
    systemctl stop camera@POE1

    log "[STOP] camera@POE_BRAMA"
    systemctl stop camera@POE_BRAMA

elif [ "$USAGE" -ge "$THRESHOLD_WARNING" ]; then
    log "[WARNING] Disk usage >= ${THRESHOLD_WARNING}%"
    send_mail_once "WARNING" "Disk usage warning: ${USAGE}%"

else
    clear_mail_state
fi
