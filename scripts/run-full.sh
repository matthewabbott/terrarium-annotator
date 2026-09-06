#!/bin/bash
# Full-quest run (all threads), bounded supervisor. At most 3 FAILED attempts
# total (repo stop condition), 30-min backoff (quota windows), each attempt
# resumes from the run_state checkpoint. Only a completed run exits 0.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR/.."
LOG=data/recordings/full-v1.log
failures=0
while true; do
    echo "$(date -Is) attempt starting (failures so far: $failures)" >> "$LOG"
    if .venv/bin/terrarium-annotator run \
        --corpus-db banished.db \
        --annotator-db data/annotator-full.db \
        --pass-id full-v1 \
        --model kimi-k2.5 \
        --record data/recordings/full-v1.jsonl \
        --timeout 900; then
        echo "$(date -Is) run completed" >> "$LOG"
        exit 0
    fi
    failures=$((failures + 1))
    echo "$(date -Is) failure #$failures recorded" >> "$LOG"
    if [ $failures -ge 3 ]; then
        echo "$(date -Is) STOPPED: 3 failed attempts (stop condition)" >> "$LOG"
        exit 1
    fi
    echo "$(date -Is) backing off 30min before resume" >> "$LOG"
    sleep 1800
done
