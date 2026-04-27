#!/bin/bash

# =========================================
# LIB_ASTRO.SH
# =========================================

source /opt/cameras.conf

ASTRO_CACHE_DIR="/opt/astro_cache"
mkdir -p "$ASTRO_CACHE_DIR"

# -----------------------------------------
# parse_offset  (-1h / +30m / 15m)
# -----------------------------------------
parse_offset() {
    local input="$1"
    input="${input// /}"

    if [[ "$input" =~ ^([+-]?)([0-9]+)([hm])$ ]]; then
        sign="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        unit="${BASH_REMATCH[3]}"

        if [ "$unit" = "h" ]; then
            seconds=$((value * 3600))
        else
            seconds=$((value * 60))
        fi

        if [ "$sign" = "-" ]; then
            echo "-$seconds"
        else
            echo "$seconds"
        fi
    else
        echo "0"
    fi
}

# -----------------------------------------
# get_astro_data_for_date YYYY-MM-DD
# zapisuje cache do pliku
# -----------------------------------------
get_astro_data_for_date() {

    local target_date="$1"
    local cache_file="$ASTRO_CACHE_DIR/$target_date.json"

    if [ -f "$cache_file" ]; then
        echo "$cache_file"
        return
    fi

    response=$(curl -s \
        "https://api.sunrise-sunset.org/json?lat=$LATITUDE&lng=$LONGITUDE&date=$target_date&formatted=0")

    sunrise=$(echo "$response" | jq -r '.results.sunrise')
    sunset=$(echo "$response" | jq -r '.results.sunset')

    if [ "$sunrise" = "null" ] || [ "$sunset" = "null" ]; then
        echo "BŁĄD: nie można pobrać danych astronomicznych"
        return 1
    fi

    sunrise_local=$(date -d "$sunrise" +%s)
    sunset_local=$(date -d "$sunset" +%s)

    jq -n \
        --arg sunrise_ts "$sunrise_local" \
        --arg sunset_ts "$sunset_local" \
        '{sunrise_ts:($sunrise_ts|tonumber), sunset_ts:($sunset_ts|tonumber)}' \
        > "$cache_file"

    echo "$cache_file"
}

# -----------------------------------------
# is_in_astro_window  (LIVE)
# -----------------------------------------
is_in_astro_window() {

    local today=$(date +%F)
    local cache_file=$(get_astro_data_for_date "$today") || return 1

    sunrise_ts=$(jq -r '.sunrise_ts' "$cache_file")
    sunset_ts=$(jq -r '.sunset_ts' "$cache_file")

    before_offset=$(parse_offset "$SUN_OFFSET_BEFORE")
    after_offset=$(parse_offset "$SUN_OFFSET_AFTER")

    start_ts=$((sunrise_ts + before_offset))
    end_ts=$((sunset_ts + after_offset))

    now_ts=$(date +%s)

    if [ "$now_ts" -ge "$start_ts" ] && [ "$now_ts" -le "$end_ts" ]; then
        return 0
    else
        return 1
    fi
}

# -----------------------------------------
# is_in_astro_window_at <timestamp>
# dla plików historycznych
# -----------------------------------------
is_in_astro_window_at() {

    local target_ts="$1"
    local target_date=$(date -d "@$target_ts" +%F)

    local cache_file=$(get_astro_data_for_date "$target_date") || return 1

    sunrise_ts=$(jq -r '.sunrise_ts' "$cache_file")
    sunset_ts=$(jq -r '.sunset_ts' "$cache_file")

    before_offset=$(parse_offset "$SUN_OFFSET_BEFORE")
    after_offset=$(parse_offset "$SUN_OFFSET_AFTER")

    start_ts=$((sunrise_ts + before_offset))
    end_ts=$((sunset_ts + after_offset))

    if [ "$target_ts" -ge "$start_ts" ] && [ "$target_ts" -le "$end_ts" ]; then
        return 0
    else
        return 1
    fi
}
