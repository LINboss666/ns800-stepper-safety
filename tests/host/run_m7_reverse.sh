#!/usr/bin/env bash
# run_m7_reverse.sh - two local M7 reverse controls (outside-repo source copies)
#   1) bypass motor_calibration_static_begin checks -> S1 PS19d/PS19b/PS19c must FAIL
#   2) publish a new calibration source without publishing new coefficients -> CC14 must FAIL
set -u -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${REV_OUT_DIR-$(env_work_root)/m7-reverse}/$RUN_ID"
OUT_DIR="$(require_outside_repo "$OUT_DIR" "REV_OUT_DIR")" || exit 4
mkdir -p "$OUT_DIR" || exit 4
PY="${PY:-python}"

# ---- copy 1: bypass motor static checks ----
MOT="$OUT_DIR/app_motor"
cp -a "$SRC_APP" "$MOT" || { echo "FATAL copy app"; exit 4; }
"$PY" - "$MOT/motor.c" <<'PY'
import sys
p=sys.argv[1]; s=open(p,encoding='utf-8').read()
old='''    e = motor_lock_take_checked();
    if (e != RT_EOK) return e;

    e = -RT_EBUSY;'''
new='''    e = motor_lock_take_checked();
    if (e != RT_EOK) return e;

    return RT_EOK; /* REVERSE-1: bypass all static checks */
    e = -RT_EBUSY;'''
assert old in s
open(p,'w',encoding='utf-8').write(s.replace(old,new))
PY
SRC_APP="$MOT" PS_OUT_DIR="$OUT_DIR/s1" bash "$HERE/run_stop_result_s1.sh" > "$OUT_DIR/s1_reverse.txt" 2>&1
s1rc=$?
hit1=0
for id in PS19b-static_armed_running_refused PS19c-static_diag_owner_refused PS19d-static_stop_unknown_refused; do
    if grep -q "CASE|$id|FAIL|" "$OUT_DIR/s1_reverse.txt"; then hit1=$((hit1+1)); fi
done
echo "REV1 s1_rc=$s1rc hits=$hit1/3"
# ---- copy 2: source-only publication ----
CFG="$OUT_DIR/app_cfg"
cp -a "$SRC_APP" "$CFG" || { echo "FATAL copy app2"; exit 4; }
"$PY" - "$CFG/project_config.c" <<'PY'
import sys
p=sys.argv[1]; s=open(p,encoding='utf-8').read()
old='''    e = cfg_apply_from(&tmp);
    if (e == RT_EOK)
    {
        cfg.cur_offset_mv = offset_mv;
        cfg.cur_gain_v_per_a = gain_v_per_a;
        cfg.cur_cal_source = source;
    }'''
new='''    /* REVERSE-2: publish new source but keep old runtime coefficients */
    e = current_adc_set_calibration_ex(cfg.cur_offset_mv, cfg.cur_gain_v_per_a,
                                       (current_adc_cal_source_t)source);
    if (e == RT_EOK)
    {
        cfg.cur_offset_mv = offset_mv;
        cfg.cur_gain_v_per_a = gain_v_per_a;
        cfg.cur_cal_source = source;
    }'''
assert old in s
open(p,'w',encoding='utf-8').write(s.replace(old,new))
PY
SRC_APP="$CFG" CC_OUT_DIR="$OUT_DIR/cc" bash "$HERE/run_current_cal_m7.sh" > "$OUT_DIR/cc_reverse.txt" 2>&1
ccrc=$?
hit2=0
if grep -q "^CASE|CC14-apply_success_measured|FAIL|" "$OUT_DIR/cc_reverse.txt"; then hit2=1; fi
echo "REV2 cc_rc=$ccrc hits=$hit2/1"
echo "OUT_DIR=$OUT_DIR"
if [ "$hit1" -eq 3 ] && [ "$hit2" -eq 1 ]; then
    echo "M7 REVERSE RESULT: PASS (both named failures detected)"; exit 0
fi
echo "M7 REVERSE RESULT: FAIL"; exit 1
