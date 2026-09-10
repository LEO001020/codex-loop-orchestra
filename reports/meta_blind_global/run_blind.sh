#!/usr/bin/env bash
set -u
D=$(mktemp -d /tmp/codex_blind_run1.XXXXXX)
echo "RUN_DIR=$D"
cd "$D" || exit 1
P=/mnt/e/codex-LOOP/codex-loop-s-f2/reports/meta_blind_global
T0=$(date +%s)
codex exec --skip-git-repo-check --sandbox read-only --json -m weiwu/deepseek-v4-flash -c model_reasoning_effort=ultra -o "$P/run1_last_message.txt" - < "$P/prompt.txt" > "$P/run1_events.jsonl" 2> "$P/run1_stderr.log"
RC=$?
T1=$(date +%s)
echo "EXIT=$RC ELAPSED=$((T1-T0))s" | tee "$P/run1_meta.txt"
echo "--- stderr ---"
cat "$P/run1_stderr.log"
echo "--- event types ---"
grep -o '"type":"[a-z_.]*"' "$P/run1_events.jsonl" | sort | uniq -c
echo "--- tail of events (usage) ---"
tail -c 1200 "$P/run1_events.jsonl"
echo ""
echo "--- last_message ---"
cat "$P/run1_last_message.txt"
