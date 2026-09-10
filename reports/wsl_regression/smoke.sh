#!/usr/bin/env bash
# POSIX smoke for tests/mock_codex shim (WSL regression dimension, read-only review)
# Runs entirely in a mktemp dir; the reviewed repo is never modified.
set -u
REPO=/mnt/e/codex-LOOP/github/codex-loop
D=$(mktemp -d /tmp/wsl_smoke.XXXXXX) || exit 90
cp -r "$REPO/tests/mock_codex" "$D/mock_codex"
chmod +x "$D/mock_codex/bin/codex" "$D/mock_codex/mock_codex_exec.sh"
pass=0; fail=0

echo "== 1. --version =="
out=$("$D/mock_codex/bin/codex" --version 2>&1); rc=$?
if [ "$rc" = 0 ] && [ "$out" = "codex-cli 0.0.0-mock" ]; then
  pass=$((pass+1)); echo "PASS version ($out)"
else
  fail=$((fail+1)); echo "FAIL version rc=$rc out=$out"
fi

echo "== 2. exec --json with -m/-c/-o (as dispatch.py builds it) =="
out=$("$D/mock_codex/bin/codex" exec --json --skip-git-repo-check --sandbox none -m weiwu-k3/kimi-k3 -c model_reasoning_effort=low -o "$D/out.txt" "run packet p1" 2>&1); rc=$?
[ "$rc" = 0 ] && { pass=$((pass+1)); echo "PASS exec rc=0"; } || { fail=$((fail+1)); echo "FAIL exec rc=$rc"; }
printf '%s\n' "$out" | grep -q '"model":"weiwu-k3/kimi-k3"' && { pass=$((pass+1)); echo "PASS model pin echoed"; } || { fail=$((fail+1)); echo "FAIL model pin"; }
printf '%s\n' "$out" | grep -q '"reasoning_effort":"low"' && { pass=$((pass+1)); echo "PASS effort echoed"; } || { fail=$((fail+1)); echo "FAIL effort"; }
grep -q "OK (mock codex" "$D/out.txt" 2>/dev/null && { pass=$((pass+1)); echo "PASS -o last message"; } || { fail=$((fail+1)); echo "FAIL -o file"; }

echo "== 3. scenario fail =="
out=$(MOCK_SCENARIO=fail "$D/mock_codex/bin/codex" exec --json "boom" 2>&1); rc=$?
[ "$rc" = 1 ] && { pass=$((pass+1)); echo "PASS fail rc=1"; } || { fail=$((fail+1)); echo "FAIL fail rc=$rc"; }

echo "== 4. report landing (supervisor runs worker with cwd=worktree) =="
mkdir -p "$D/wt1"
(cd "$D/wt1" && "$D/mock_codex/bin/codex" exec --json "land data/reports/p1/report.json please" >/dev/null 2>&1); rc=$?
[ "$rc" = 0 ] && { pass=$((pass+1)); echo "PASS landing rc=0"; } || { fail=$((fail+1)); echo "FAIL landing rc=$rc"; }
if [ -f "$D/wt1/data/reports/p1/report.json" ]; then
  pass=$((pass+1)); echo "PASS report file landed"
  grep -q '"packet_id":"p1"' "$D/wt1/data/reports/p1/report.json" && { pass=$((pass+1)); echo "PASS report content"; } || { fail=$((fail+1)); echo "FAIL report content"; }
else
  fail=$((fail+1)); echo "FAIL report file missing"
fi

echo "== 5. scenario_control.sh set/get/reset =="
"$D/mock_codex/scenario_control.sh" set fail >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && [ "$(cat "$D/mock_codex/.state/scenario")" = "fail" ] && { pass=$((pass+1)); echo "PASS set fail"; } || { fail=$((fail+1)); echo "FAIL set rc=$rc"; }
"$D/mock_codex/scenario_control.sh" reset >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && [ "$(cat "$D/mock_codex/.state/scenario")" = "normal" ] && { pass=$((pass+1)); echo "PASS reset"; } || { fail=$((fail+1)); echo "FAIL reset rc=$rc"; }

echo "RESULT: pass=$pass fail=$fail"
echo "SMOKE_DIR=$D"
exit 0
