#!/usr/bin/env bash
# Run spikes 1-6 in order. A failing spike does not stop the rest. Extra
# arguments are passed to every spike (e.g. --no-reap).
#
#   SOLARI_API_KEY=... ./spikes/run_all.sh
set -u
cd "$(dirname "$0")"
: "${SOLARI_API_KEY:?set SOLARI_API_KEY (https://console.getsolari.com)}"
PY="${PYTHON:-python}"

pass=()
fail=()
t_all=$(date +%s)
for f in spike_0{1..6}_*.py; do
  echo
  echo "=================== $f ==================="
  t0=$(date +%s)
  if "$PY" "$f" "$@"; then
    pass+=("$f")
  else
    fail+=("$f (exit $?)")
  fi
  echo "--- $f finished in $(( $(date +%s) - t0 ))s"
done

echo
echo "=================== summary ($(( $(date +%s) - t_all ))s) ==================="
echo "passed: ${#pass[@]}"
for p in "${pass[@]:-}"; do [ -n "$p" ] && echo "  ok    $p"; done
echo "failed: ${#fail[@]}"
for p in "${fail[@]:-}"; do [ -n "$p" ] && echo "  FAIL  $p"; done
if [ -f results.jsonl ]; then
  echo
  echo "last results (results.jsonl):"
  tail -n 40 results.jsonl
fi
# Belt and braces: nothing tagged forkloop=spike should be left running.
"$PY" _common.py --reap || true
[ "${#fail[@]}" -eq 0 ]
