#!/usr/bin/env bash
# Run restore.sh N times and print p50/p95/p99 of the wall-clock restore time.
#   ./bench_local.sh 10
set -euo pipefail
cd "$(dirname "$0")"
N=${1:-5}

times=()
for i in $(seq 1 "$N"); do
  echo "--- restore $i/$N"
  out=$(./restore.sh | tail -n 1)
  echo "$out"
  times+=("${out#restore: }")
done

printf '%s\n' "${times[@]}" | python3 -c '
import sys, math
xs = sorted(float(l.rstrip("s\n")) for l in sys.stdin if l.strip())
def pct(p):
    k = (len(xs) - 1) * p / 100; f, c = math.floor(k), math.ceil(k)
    return xs[f] if f == c else xs[f] + (xs[c] - xs[f]) * (k - f)
print(f"local restore  n={len(xs)}  p50={pct(50):.1f}s  p95={pct(95):.1f}s  p99={pct(99):.1f}s  min={xs[0]:.1f}s  max={xs[-1]:.1f}s")
'
