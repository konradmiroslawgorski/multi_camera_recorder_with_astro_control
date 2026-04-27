#!/bin/bash

BASE="/opt/recordings/live"
TODAY=$(date +%Y-%m-%d)

DIR="$BASE/$TODAY"

if [ -d "$DIR" ]; then
    echo "Przetwarzam dzisiejszy katalog: $DIR"
    /opt/batch_sun_precise.sh "$DIR"
else
    echo "Brak katalogu $DIR"
fi
