#!/bin/bash
# Threads 1-40 run, bounded supervisor. Stop condition (repo-wide): at most
# 3 FAILED attempts total, then halt and preserve the failure log. Only a
# completed run exits 0. Each failure resumes from the run_state checkpoint.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR/.."
THREADS=$(python3 -c "import json; print(','.join(str(t['id']) for t in json.load(open('data/exports/threads-first-40.json'))['threads']))")
LOG=data/recordings/t1-40.log
failures=0
while true; do
    echo "$(date -Is) attempt starting (failures so far: $failures)" >> "$LOG"
    if .venv/bin/terrarium-annotator run \
        --corpus-db banished.db \
        --annotator-db data/annotator-t1-40.db \
        --threads "$THREADS" \
        --pass-id t1-40 \
        --model kimi-k2.5 \
        --record data/recordings/t1-40.jsonl \
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
