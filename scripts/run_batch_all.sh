#!/bin/bash

BASE="/opt/recordings/live"

for dir in "$BASE"/20*; do
    if [ -d "$dir" ]; then
        echo "Przetwarzam $dir"
        /opt/batch_sun_precise.sh "$dir"
    fi
done
