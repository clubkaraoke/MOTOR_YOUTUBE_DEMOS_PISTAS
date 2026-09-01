#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE="${WAVE_PEAKS_BASE:-$HOME/djgabo-wave-peaks/tools/wave-peaks}"
ENV_FILE="${WAVE_PEAKS_ENV:-$HOME/.config/djgabo-wave-peaks.env}"
MANIFEST="${WAVE_PEAKS_MANIFEST:-$BASE/missing_peaks_v3_drive_file_ids.txt}"
MAP="${WAVE_PEAKS_MAP:-$BASE/demo-map.json}"
GENERATOR="${WAVE_PEAKS_GENERATOR:-$BASE/generate_peaks.py}"
STATE="${WAVE_PEAKS_STATE:-$BASE/wave-peaks-safe-state.jsonl}"
AUTO_LOG="${WAVE_PEAKS_AUTO_LOG:-$BASE/wave-peaks-auto.log}"
ROOT_DIR="${WAVE_PEAKS_ROOT:-$HOME/djgabo-wave-peaks}"
LOCK_FILE="$ROOT_DIR/.wave-peaks-auto.lock"
PAUSE_FILE="$ROOT_DIR/AUTO_PAUSED"
COMPLETE_FILE="$ROOT_DIR/AUTO_COMPLETE"

BATCH_SIZE="${BATCH_SIZE:-200}"
DELAY_SUCCESS="${DELAY_SUCCESS:-10}"
RESUME_AUTO="${RESUME_AUTO:-false}"

timestamp() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() {
  printf '%s %s\n' "$(timestamp)" "$*" | tee -a "$AUTO_LOG"
}

case "$BATCH_SIZE" in
  ''|*[!0-9]*) echo "ERROR: BATCH_SIZE must be an integer" >&2; exit 2 ;;
esac
if (( BATCH_SIZE < 1 || BATCH_SIZE > 200 )); then
  echo "ERROR: BATCH_SIZE must be between 1 and 200" >&2
  exit 2
fi

case "$DELAY_SUCCESS" in
  ''|*[!0-9]*) echo "ERROR: DELAY_SUCCESS must be an integer" >&2; exit 2 ;;
esac
if (( DELAY_SUCCESS < 10 )); then
  echo "ERROR: DELAY_SUCCESS cannot be lower than 10 seconds" >&2
  exit 2
fi

mkdir -p "$ROOT_DIR" "$BASE"
touch "$AUTO_LOG"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "AUTO_SKIP reason=lock_busy"
  exit 0
fi

if pgrep -af '[g]enerate_peaks.py' >/dev/null 2>&1; then
  log "AUTO_SKIP reason=generator_already_running"
  exit 0
fi

if [[ "$RESUME_AUTO" == "true" ]]; then
  if [[ -f "$PAUSE_FILE" ]]; then
    rm -f "$PAUSE_FILE"
    log "AUTO_RESUME pause_marker_removed=true"
  fi
fi

if [[ -f "$PAUSE_FILE" ]]; then
  log "AUTO_PAUSED marker=$PAUSE_FILE"
  cat "$PAUSE_FILE"
  exit 0
fi

if [[ -f "$COMPLETE_FILE" ]]; then
  log "AUTO_COMPLETE_ALREADY marker=$COMPLETE_FILE"
  cat "$COMPLETE_FILE"
  exit 0
fi

for required in "$ENV_FILE" "$MANIFEST" "$MAP" "$GENERATOR"; do
  if [[ ! -f "$required" ]]; then
    log "AUTO_PAUSE reason=missing_required_file path=$required"
    printf '%s missing_required_file %s\n' "$(timestamp)" "$required" > "$PAUSE_FILE"
    exit 3
  fi
done

if find "$ENV_FILE" -perm /077 -print -quit | grep -q .; then
  log "AUTO_PAUSE reason=env_permissions_too_open"
  printf '%s env_permissions_too_open\n' "$(timestamp)" > "$PAUSE_FILE"
  exit 3
fi

help_text="$(python3 "$GENERATOR" --help 2>&1 || true)"
for flag in --ids-file --limit --delay-success; do
  if ! grep -q -- "$flag" <<<"$help_text"; then
    log "AUTO_PAUSE reason=unsafe_generator_missing_flag flag=$flag"
    printf '%s unsafe_generator_missing_flag %s\n' "$(timestamp)" "$flag" > "$PAUSE_FILE"
    exit 3
  fi
done

if grep -q -- '--force' <<<"$help_text"; then
  log "AUTO_PAUSE reason=unsafe_generator_force_present"
  printf '%s unsafe_generator_force_present\n' "$(timestamp)" > "$PAUSE_FILE"
  exit 3
fi

if ! grep -q 'CIRCUIT_BREAKER_STOP' "$GENERATOR" || ! grep -q 'SKIP_R2' "$GENERATOR"; then
  log "AUTO_PAUSE reason=safe_markers_missing"
  printf '%s safe_markers_missing\n' "$(timestamp)" > "$PAUSE_FILE"
  exit 3
fi

# Load credentials only after all local safety checks. Values are never printed.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

batch_file="$(mktemp "$ROOT_DIR/.wave-peaks-batch.XXXXXX.txt")"
delta_file="$(mktemp "$ROOT_DIR/.wave-peaks-state-delta.XXXXXX.jsonl")"
run_output="$(mktemp "$ROOT_DIR/.wave-peaks-run-output.XXXXXX.log")"
cleanup() {
  rm -f "$batch_file" "$delta_file" "$run_output"
}
trap cleanup EXIT

readarray -t stats < <(
python3 - "$MANIFEST" "$STATE" "$batch_file" "$BATCH_SIZE" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
state_path = Path(sys.argv[2])
batch_path = Path(sys.argv[3])
batch_size = int(sys.argv[4])

ids = []
seen = set()
for raw in manifest_path.read_text(encoding="utf-8").splitlines():
    fid = raw.strip()
    if fid and fid not in seen:
        seen.add(fid)
        ids.append(fid)

completed = set()
error_counts = {}
if state_path.exists():
    for raw in state_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        fid = str(obj.get("fileId") or "").strip()
        status = str(obj.get("status") or "").strip()
        if not fid:
            continue
        if status in {"SUCCESS", "SKIP_R2"}:
            completed.add(fid)
        elif status == "ERROR":
            error_counts[fid] = error_counts.get(fid, 0) + 1

quarantined = {
    fid for fid, count in error_counts.items()
    if count >= 3 and fid not in completed
}
pending = [fid for fid in ids if fid not in completed and fid not in quarantined]
selected = pending[:batch_size]
batch_path.write_text(
    "".join(f"{fid}\n" for fid in selected),
    encoding="utf-8",
)

print(len(ids))
print(len(completed & set(ids)))
print(len(quarantined & set(ids)))
print(len(pending))
print(len(selected))
PY
)

manifest_unique="${stats[0]:-0}"
completed_count="${stats[1]:-0}"
quarantined_count="${stats[2]:-0}"
pending_count="${stats[3]:-0}"
selected_count="${stats[4]:-0}"

log "AUTO_PLAN manifest_unique=$manifest_unique completed_state=$completed_count quarantined=$quarantined_count pending=$pending_count selected=$selected_count batch_size=$BATCH_SIZE delay=$DELAY_SUCCESS"

if (( selected_count == 0 )); then
  if (( quarantined_count > 0 )); then
    printf '%s only_quarantined_remaining count=%s\n' "$(timestamp)" "$quarantined_count" > "$PAUSE_FILE"
    log "AUTO_PAUSE reason=only_quarantined_remaining count=$quarantined_count"
    exit 4
  fi
  printf '%s complete manifest_unique=%s\n' "$(timestamp)" "$manifest_unique" > "$COMPLETE_FILE"
  log "AUTO_COMPLETE manifest_unique=$manifest_unique"
  exit 0
fi

state_before=0
if [[ -f "$STATE" ]]; then
  state_before="$(wc -l < "$STATE")"
fi

log "AUTO_START selected=$selected_count"
set +e
(
  cd "$BASE"
  nice -n 10 python3 "$GENERATOR"     --map "$MAP"     --ids-file "$batch_file"     --limit "$selected_count"     --delay-success "$DELAY_SUCCESS"
) 2>&1 | tee "$run_output"
generator_rc=${PIPESTATUS[0]}
set -e

if [[ -f "$STATE" ]]; then
  tail -n "+$((state_before + 1))" "$STATE" > "$delta_file" || true
fi

readarray -t run_stats < <(
python3 - "$delta_file" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

path = Path(sys.argv[1])
counts = Counter()
if path.exists():
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        counts[str(obj.get("status") or "UNKNOWN")] += 1

for key in ("SKIP_R2", "SUCCESS", "RETRY", "ERROR", "CIRCUIT_BREAKER_STOP"):
    print(counts.get(key, 0))
PY
)

skip_count="${run_stats[0]:-0}"
success_count="${run_stats[1]:-0}"
retry_count="${run_stats[2]:-0}"
error_count="${run_stats[3]:-0}"
breaker_count="${run_stats[4]:-0}"

log "AUTO_RESULT rc=$generator_rc SKIP_R2=$skip_count SUCCESS=$success_count RETRY=$retry_count ERROR=$error_count CIRCUIT_BREAKER_STOP=$breaker_count"

if (( breaker_count > 0 )); then
  printf '%s circuit_breaker_stop\n' "$(timestamp)" > "$PAUSE_FILE"
  log "AUTO_PAUSE reason=circuit_breaker_stop"
  exit 10
fi

if (( generator_rc != 0 )); then
  printf '%s generator_exit_%s\n' "$(timestamp)" "$generator_rc" > "$PAUSE_FILE"
  log "AUTO_PAUSE reason=generator_nonzero rc=$generator_rc"
  exit "$generator_rc"
fi

# Additional automation-level brake for scattered instability that may not be
# consecutive enough to trip the generator's own circuit breaker.
if (( retry_count >= 3 )); then
  printf '%s excessive_retries count=%s\n' "$(timestamp)" "$retry_count" > "$PAUSE_FILE"
  log "AUTO_PAUSE reason=excessive_retries count=$retry_count"
  exit 11
fi

if (( error_count >= 5 )); then
  printf '%s excessive_errors count=%s\n' "$(timestamp)" "$error_count" > "$PAUSE_FILE"
  log "AUTO_PAUSE reason=excessive_errors count=$error_count"
  exit 12
fi

log "AUTO_BATCH_COMPLETE selected=$selected_count"
