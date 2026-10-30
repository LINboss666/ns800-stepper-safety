#!/usr/bin/env bash
# run_current_cal_m7.sh - M7 current_cal host chain (offline, no device)
# Compiles the real current_adc.c / project_config.c / current_cal.c through
# cc_main.c; motor/sensor/storage are injectable fakes.
set -u -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${CC_OUT_DIR-$(env_work_root)/current-cal-m7}/$RUN_ID"
OUT_DIR="$(require_outside_repo "$OUT_DIR" "CC_OUT_DIR")" || exit 4
mkdir -p "$OUT_DIR" || exit 4
GCC="$(env_detect_gcc || true)"
[ -n "$GCC" ] || { echo "FATAL: no gcc"; exit 127; }
[ -f "$HERE/cc_main.c" ] || { echo "FATAL: cc_main.c missing"; exit 127; }
FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function -DPS_LEAF_SCRIPTED_TIME -Drt_kprintf=cc_kprintf"
"$GCC" $FLAGS -I "$HERE/shim_cc" -I "$SRC_APP" "$HERE/cc_main.c" "$SRC_APP/diag_relative.c" \
    -o "$OUT_DIR/cc.exe" > "$OUT_DIR/build.txt" 2>&1
brc=$?
if [ "$brc" -ne 0 ]; then
    echo "FATAL build rc=$brc"; tail -30 "$OUT_DIR/build.txt"; exit 4
fi
"$OUT_DIR/cc.exe" > "$OUT_DIR/out.txt" 2>&1
prc=$?
echo "build_rc=$brc proc_rc=$prc"
cat "$OUT_DIR/out.txt"
ids="CC01-show_no_side_effects CC02-sample_128_normal CC03-sample_dup_invalid_skip CC04-sample_endpoint_rejected CC05-sample_no_frames_bounded CC06-isr_silent CC07-masked_silent CC08-no_thread_silent CC09-bad_n_usage CC10-missing_confirm_usage CC11-bad_nominal_confirm CC12-apply_numeric_reject CC13-apply_static_refused CC14-apply_success_measured CC15-apply_other_cfg_kept CC16-nominal_theoretical CC17-config_publish_order CC18-save_load_preserves_measured CC19-load_failure_theoretical CC20-cal_snapshot_same_read CC21-key_lines_ascii_110 CC22-msh_export_name CC23-sample_cal_changed CC24-sample_256_boundary CC25-nan_inf_range_reject CC26-api_none_legal_config_rejects CC27-cfg_lock_unavailable_rejects CC27b-cfg_lock_unavailable_no_mutation CC27c-cfg_lock_unavailable_no_release CC27d-cfg_lock_available_publishes CC28-microamp_export_no_int32_wrap CC28b-microamp_line_still_short CC28c-microamp_negative_signed CC29-scaled_dec_range_marker"
n=0; bad=0
for id in $ids; do
    n=$((n+1))
    cnt=$(grep -c "^CASE|$id|" "$OUT_DIR/out.txt" || true)
    pas=$(grep -c "^CASE|$id|PASS|" "$OUT_DIR/out.txt" || true)
    if [ "$cnt" -ne 1 ] || [ "$pas" -ne 1 ]; then
        echo "  FAIL ledger $id cnt=$cnt pass=$pas"; bad=1
    fi
done
sum="$(grep -m1 '^SUMMARY|' "$OUT_DIR/out.txt")"
echo "ledger=$n/$n bad=$bad $sum"
[ "$bad" -eq 0 ] && [ "$prc" -eq 0 ] && \
    echo "$sum" | grep -q "cases=$n pass=$n fail=0 pre_bad=0"
rc=$?
if [ "$rc" -eq 0 ]; then echo "M7 CURRENT_CAL RESULT: PASS"; else echo "M7 CURRENT_CAL RESULT: FAIL"; fi
exit "$rc"
