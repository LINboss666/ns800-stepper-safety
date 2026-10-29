#!/usr/bin/env bash
# run_m6_cli.sh - M6 C1 CLI strict-argument host chain (offline, no hardware)
# Compiles the real ps_main.c in PS_CLI_ONLY mode (real motor.c/step_pwm.c/
# safety_state.c) and runs target + host_ovr variants.
set -u -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
HAL_DRV="${HAL_DRV:-$REPO_ROOT/rt-thread-master/rt-thread-master/bsp/novosns/ns800/libraries/HAL_Drivers/drivers}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${CLI_OUT_DIR-$(env_work_root)/m6-cli}/$RUN_ID"
OUT_DIR="$(require_outside_repo "$OUT_DIR" "CLI_OUT_DIR")" || exit 4
mkdir -p "$OUT_DIR/compat" || exit 4
printf '#ifndef C\n#define C\n#include <rtthread.h>\n#endif\n' > "$OUT_DIR/compat/rtdef.h"
printf '#ifndef H\n#define H\n#include <rtthread.h>\nrt_base_t rt_hw_interrupt_disable(void);\nvoid rt_hw_interrupt_enable(rt_base_t);\n#endif\n' > "$OUT_DIR/compat/rthw.h"
GCC="$(env_detect_gcc || true)"
[ -n "$GCC" ] || { echo "FATAL: no gcc"; exit 127; }
FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function"
common="PS18a-motor_dir_cli_strict PS18a2-motor_dir_valid_writes PS18b-motor_target_cli_strict PS18b2-motor_target_valid_bounds PS18c-arm_start_extra_args_zero_action PS18f-key_status_lines_no_truncation PS18g-safety_longest_enum_line"
target_ids="$common PS18d-target_gate_refuses_valid_arm PS18e-valid_cli_cleanup"
ovr_ids="$common PS18d-arm_start_valid_reaches_api PS18e-valid_cli_cleanup"
rc_all=0
validate() {
    local out="$1" ids="$2" id cnt pass n=0 sum
    for id in $ids; do
        n=$((n+1)); cnt=$(grep -c "^CASE|$id|" "$out" || true); pass=$(grep -c "^CASE|$id|PASS|" "$out" || true)
        if [ "$cnt" -ne 1 ] || [ "$pass" -ne 1 ]; then echo "  FAIL ledger $id cnt=$cnt pass=$pass"; return 1; fi
    done
    sum=$(grep -m1 '^SUMMARY|' "$out")
    echo "  ledger $n/$n PASS; $sum"
    echo "$sum" | grep -q "cases=$n pass=$n fail=0 pre_bad=0"
}
for v in target host_ovr; do
    extra=""
    [ "$v" = host_ovr ] && extra="-DPS_GATE_HOST_OVERRIDE -DMOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_TRUE -DSTEP_DIAG_ISOLATED_ENV_CONFIRMED=RT_TRUE -DTMC_DRIVE_CONFIG_HW_VALIDATED=RT_TRUE"
    # shellcheck disable=SC2086
    "$GCC" $FLAGS -DPS_CLI_ONLY $extra -I "$HERE/shim_ps" -I "$SRC_APP" -I "$HAL_DRV" -I "$OUT_DIR/compat" "$HERE/ps_main.c" -o "$OUT_DIR/$v.exe" > "$OUT_DIR/${v}_build.txt" 2>&1 || { echo "FATAL build $v"; tail -10 "$OUT_DIR/${v}_build.txt"; exit 4; }
    "$OUT_DIR/$v.exe" > "$OUT_DIR/${v}_out.txt" 2>&1; prc=$?
    ids="$target_ids"; [ "$v" = host_ovr ] && ids="$ovr_ids"
    echo "=== $v rc=$prc ==="
    if ! validate "$OUT_DIR/${v}_out.txt" "$ids"; then rc_all=1; fi
    [ "$prc" -eq 0 ] || rc_all=1
done
if [ "$rc_all" -eq 0 ]; then echo "M6 CLI RESULT: PASS"; else echo "M6 CLI RESULT: FAIL"; fi
exit "$rc_all"
