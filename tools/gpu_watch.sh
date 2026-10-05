#!/usr/bin/env bash
# Run a command while sampling GPU load once per second, then summarise.
# AMD: /sys/class/drm/card*/device/gpu_busy_percent; NVIDIA: nvidia-smi.
#   tools/gpu_watch.sh <command...>
set -u
log=$(mktemp)
if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits -l 1 >"$log" &
else
    busy=$(ls /sys/class/drm/card*/device/gpu_busy_percent | head -1)
    (while :; do cat "$busy"; sleep 1; done) >"$log" &
fi
sampler=$!
"$@"
status=$?
kill "$sampler" 2>/dev/null
awk '{s+=$1; if ($1>20) b++} END {if (NR) printf "GPU: avg %.0f%% busy, %d/%d samples >20%%\n", s/NR, b, NR}' "$log" >&2
rm -f "$log"
exit $status
