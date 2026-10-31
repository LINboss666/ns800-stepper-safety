#!/usr/bin/env bash
# run_diag_relative_m8.sh - M8/M8-R1 SG/ADC 相对保护 + 碰撞独立保护的主机验证链(离线, 无设备)
#
# 真编译并执行(不是桩):
#   applications/sensor_service.c  —— SG/ADC 样本身份(sg_seq/cur_seq/tick)的生成
#   applications/project_config.c  —— v3 profile 持久化 + v2→v3 迁移 + 半套拒绝
#   applications/diag_relative.c   —— 相对判定引擎 / profile 资格 / 先保护后证据
#   applications/diagnosis.c       —— RELATIVE 开启门禁 / 线程覆盖披露 / 状态行
# 夹具 tests/host/rel_main.c 里的时间模型是**虚拟时钟**: rt_tick_get 由夹具推进,
# 生产线程体(diag_thread_entry / sensor_thread_entry)通过 setjmp/longjmp 按迭代预算
# 驱动, 两线程严格对在同一 10ms tick 上。据此只能宣称"判据与时序逻辑"通过,
# **不能**宣称真实时序精度或抢占式并发已验证(见交接的 artifact 分级)。
#
# 判定纪律(与 SD/CC 链同一口径):
#   1) 在任何 rm/mkdir/重定向/编译**之前**先验证工作目录(require_outside_repo);
#   2) 只删除带本工具哨兵(NS800-HOSTCHAIN-WORK)的目录;
#   3) ONLY 选择词必须在入口校验: 未知/重复/纯空白一律 rc=4 拒绝且不动任何文件;
#      未设置或空串 = 默认执行全部 50 例(不是零执行); 汇总只声明实际执行集合,
#      未执行的显式 NOT_RUN;
#   4) 逐 id 台账必须"恰好出现一次且判 PASS"; 缺席/重复/未登记/SUMMARY 不自洽
#      = 基础设施故障(4); 编译失败或异常退出(非 0/1/4)也不算检出;
#   5) 反向验证: 副本注入锚点必须**恰好命中一次**, 带 NEGATIVE-INJECTION 标记,
#      副本编译成功 + 夹具 rc=1 + 完整自洽台账 + 指定业务失败身份命中 = META-OK;
#      rc=0 或缺失期望身份 = NOT-DETECTED(1)。
#
# 用法: bash tests/host/run_diag_relative_m8.sh
#       REL_OUT_DIR=<仓外目录> SRC_APP=<快照目录> GCC=<编译器>
#       ONLY="REL08-stale-sg-cannot-accumulate REL09-adc-severe-both-directions"
#       REVERSE=1
#       REL_VALIDATE_FILE=<既有 out.txt>      # 只复核台账, 不编译不运行
# 退出码: 0=通过 1=检出失败 4=基础设施/参数故障 127=环境缺失

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_rel}"
REL_MAIN="${REL_MAIN:-$HERE/rel_main.c}"
OUT_DIR_BASE="${REL_OUT_DIR-$(env_work_root)/m8}"
ONLY="${ONLY:-}"
REVERSE="${REVERSE:-0}"
PY="${PY:-python}"
SENTINEL="NS800-HOSTCHAIN-WORK"

# ---- 用例台账(必须与 rel_main.c 的 TAB[] 一字不差) ----
ALL_CASES="REL01-frame-identity-advances-only-on-success
REL02-sg-read-fail-freezes-identity
REL03-adc-read-fail-freezes-identity
REL04-no-profile-no-claim
REL05-profile-qualification-matrix
REL06-live-signature-binds-drive-config
REL07-sg-severe-needs-samples-and-span
REL08-stale-sg-cannot-accumulate
REL09-adc-severe-both-directions
REL10-single-adc-sample-not-duration
REL11-fused-needs-overlap
REL12-reason-not-stall-confirmed
REL13-protect-before-print
REL14-first-latch-immutable
REL15-monitor-only-never-acts
REL16-relative-mode-arm-gate
REL17-active-measured-gate-unchanged
REL18-ramp-phase-is-exception
REL19-out-of-window-only-in-motion
REL20-age-check-before-dedup
REL21-recovery-needs-fresh-evidence
REL22-coverage-loss-protects-first
REL23-profile-persist-and-v2-migration
REL24-cli-strict-args-no-side-effects
REL25-cli-capture-and-publish
REL26-tick-diff-and-threshold-bounds
REL27-active-severe-protect-before-print
REL28-strong-needs-own-evidence
REL29-frozen-adc-age-degrades
REL30-source-gap-restarts-evidence
REL31-tick-wrap-and-rate-binding
REL32-recovery-breaks-on-counter-evidence
REL33-lock-fail-bounded-degrade
REL34-no-frame-still-supervises
REL35-incident-ticket-lifecycle
REL36-null-outparams-still-protects
REL37-post-failure-keeps-first-cause
REL38-mode-gate-and-static-transaction
REL39-zero-static-and-failed-relearn
REL40-config-lock-failure-writes-nothing
REL41-illegal-context-silent
REL42-low-sg-committable-and-named-reject
REL43-max-value-lines-fit-console
REL44-impact-fresh-evidence-only
REL45-ctl-take-failure-writes-nothing
REL46-ctl-uninit-take-and-protect
REL47-commit-inside-static-gate
REL48-new-generation-clears-evidence
REL49-frame-not-same-as-motion-known
REL50-stale-idle-not-static-exemption"
ALL_CASES="$(printf '%s' "$ALL_CASES" | tr '\n' ' ')"

note() { printf '%s\n' "$*"; }
remember() { FINAL_RC="$1"; }
declare -a EXECUTED=()
declare -a MATRIX=()
FINAL_RC=0

# ---------- ONLY 选择校验(在任何文件操作之前) ----------
SELECTED=()
SELECT_MODE="default"
validate_only() {
    local tok s
    SELECTED=()
    if [ -z "$ONLY" ]; then
        for tok in $ALL_CASES; do SELECTED+=("$tok"); done
        SELECT_MODE="default"
        return 0
    fi
    set -- $ONLY
    if [ "$#" -eq 0 ]; then
        note "REJECT(ONLY): 设置了 ONLY 但没有有效选择词(只含空白) —— 不能当作已执行"
        return 1
    fi
    for tok in "$@"; do
        case " $ALL_CASES " in
            *" $tok "*) : ;;
            *) note "REJECT(ONLY): 未知用例 id '$tok'"; return 1 ;;
        esac
        for s in ${SELECTED[@]+"${SELECTED[@]}"}; do
            if [ "$s" = "$tok" ]; then note "REJECT(ONLY): 重复词 '$tok'"; return 1; fi
        done
        SELECTED+=("$tok")
    done
    SELECT_MODE="explicit"
    return 0
}

# ---------- 台账与 SUMMARY 合同校验 ----------
LED_MISSING=0; LED_DUP=0; LED_UNREG=0; LED_REQ_FAILS=0
LED_CASES=0; LED_PASSN=0; LED_FAILN=0; LED_SUMMARIES=0
LED_REQ=""
validate_ledger() {                               # validate_ledger <out> <必需 id 列表>
    local out="$1" r id cnt sumline
    LED_REQ="$2"
    LED_MISSING=0; LED_DUP=0; LED_UNREG=0; LED_REQ_FAILS=0
    if [ ! -s "$out" ]; then note "  台账: 输出缺失或为空: $out"; return 4; fi
    LED_CASES="$(grep -cE '^CASE\|' "$out")"
    LED_PASSN="$(grep -cE '^CASE\|[^|]*\|PASS\|' "$out")"
    LED_FAILN="$(grep -cE '^CASE\|[^|]*\|FAIL\|' "$out")"
    LED_SUMMARIES="$(grep -cE '^SUMMARY\|' "$out")"
    if [ "$LED_SUMMARIES" -ne 1 ]; then
        note "  台账: SUMMARY $LED_SUMMARIES 条(必须恰好 1 条)"; return 4
    fi
    sumline="$(grep -E '^SUMMARY\|' "$out" | head -1)"
    local sc sp sf
    sc="$(printf '%s' "$sumline" | sed -n 's/.*cases=\([0-9]*\).*/\1/p')"
    sp="$(printf '%s' "$sumline" | sed -n 's/.*pass=\([0-9]*\).*/\1/p')"
    sf="$(printf '%s' "$sumline" | sed -n 's/.*fail=\([0-9]*\).*/\1/p')"
    if [ "${sc:-X}" != "$LED_CASES" ] || [ "${sp:-X}" != "$LED_PASSN" ] || \
       [ "${sf:-X}" != "$LED_FAILN" ] || [ "$(( ${sp:-0} + ${sf:-0} ))" -ne "${sc:-0}" ]; then
        note "  台账: SUMMARY 与实际行不一致 ($sumline) 实际 cases=$LED_CASES pass=$LED_PASSN fail=$LED_FAILN"
        return 4
    fi
    for r in $LED_REQ; do
        cnt="$(grep -c "^CASE|$r|" "$out")"
        if [ "$cnt" -eq 0 ]; then LED_MISSING=$((LED_MISSING+1)); note "  缺席: $r"
        elif [ "$cnt" -gt 1 ]; then LED_DUP=$((LED_DUP+1)); note "  重复执行: $r ($cnt 次)"; fi
    done
    for id in $(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u); do
        case " $LED_REQ " in *" $id "*) : ;; *) LED_UNREG=$((LED_UNREG+1)); note "  未登记用例: $id" ;; esac
    done
    if [ "$LED_UNREG" -ne 0 ]; then note "  台账: $LED_UNREG 个未登记用例"; return 4; fi
    if [ "$LED_MISSING" -ne 0 ] || [ "$LED_DUP" -ne 0 ]; then
        note "  台账: 缺席=$LED_MISSING 重复=$LED_DUP"; return 4
    fi
    for r in $LED_REQ; do
        [ "$(grep -c "^CASE|$r|PASS|" "$out")" = "1" ] || LED_REQ_FAILS=$((LED_REQ_FAILS+1))
    done
    return 0
}

# ---------- 入口参数验证(先于一切文件操作) ----------
if ! validate_only; then
    note "M8 RELATIVE RESULT: FAIL(参数被拒绝, 未执行任何文件操作)"
    exit 4
fi
if [ "$REVERSE" = "1" ] && [ -n "$ONLY" ]; then
    note "REJECT: REVERSE=1 不接受 ONLY 组合(反向必须跑完整副本链)"
    exit 4
fi
if [ -n "${REL_VALIDATE_FILE:-}" ]; then
    f="$REL_VALIDATE_FILE"
    if validate_ledger "$f" "$ALL_CASES"; then
        note "M8 台账复核: 结构自洽 必需失败=$LED_REQ_FAILS/$(( ${#SELECTED[@]} )) rc-expected=?"
        [ "$LED_REQ_FAILS" -eq 0 ] && { echo "M8 RELATIVE RESULT: PASS(仅台账复核)"; exit 0; }
        echo "M8 RELATIVE RESULT: FAIL(台账复核发现失败用例)"; exit 1
    fi
    echo "M8 RELATIVE RESULT: FAIL(台账合同不成立)"; exit 4
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
WORK_RAW="$OUT_DIR_BASE/$RUN_ID"
OUT_DIR="$(require_outside_repo "$WORK_RAW" "REL_OUT_DIR")" || {
    note "FATAL: 工作目录被拒绝, 未创建任何目录"; exit 4; }
if [ -e "$OUT_DIR" ]; then
    note "FATAL: 工作目录已存在: $OUT_DIR"; exit 4
fi
mkdir -p "$OUT_DIR" || exit 4
: > "$OUT_DIR/.$SENTINEL" || exit 4

GCC="$(env_detect_gcc || true)"
[ -n "$GCC" ] || { note "FATAL: 未找到主机编译器(设 GCC=<路径>)"; exit 127; }
[ -f "$REL_MAIN" ] || { note "FATAL: $REL_MAIN 缺失"; exit 127; }
[ -d "$SHIM_DIR" ] || { note "FATAL: $SHIM_DIR 缺失"; exit 127; }
FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter"
note "compiler=$("$GCC" --version | head -1)"
note "src_app=$SRC_APP"
note "out_dir=$OUT_DIR"
note "selected=${#SELECTED[@]} mode=$SELECT_MODE"

# ---------- 正常链 ----------
EXE="$OUT_DIR/rel.exe"; OUT="$OUT_DIR/out.txt"
"$GCC" $FLAGS -I "$SHIM_DIR" -I "$SRC_APP" "$REL_MAIN" -o "$EXE" > "$OUT_DIR/build.txt" 2>&1
brc=$?
if [ "$brc" -ne 0 ]; then
    tail -40 "$OUT_DIR/build.txt"
    note "FATAL: 编译失败 rc=$brc(没有运行任何旧产物)"; exit 4
fi
grep -c "warning" "$OUT_DIR/build.txt" > "$OUT_DIR/warning_count.txt" 2>&1 || true
note "build_rc=$brc warnings=$(grep -c 'warning:' "$OUT_DIR/build.txt" || true)"

# 夹具的选择参数走 argv(只接受已知 id, 未知 id 由夹具判 pre_bad => rc=4)
"$EXE" "${SELECTED[*]}" > "$OUT" 2>&1
prc=$?
EXECUTED+=("normal")
note "proc_rc=$prc"
cat "$OUT" | grep -E "^CASE\||^SUMMARY\||^INFRA\|" || true
if [ "$prc" -ne 0 ] && [ "$prc" -ne 1 ] && [ "$prc" -ne 4 ]; then
    note "FATAL: 夹具异常退出 rc=$prc(非 0/1/4 合同值)"; exit 4
fi
if validate_ledger "$OUT" "${SELECTED[*]}"; then
    if [ "$LED_REQ_FAILS" -eq 0 ] && [ "$prc" -eq 0 ]; then
        MATRIX+=("normal | PASS 执行=${#SELECTED[@]} 全部通过 proc_rc=0")
        remember 0
    else
        MATRIX+=("normal | FAIL 必需失败=$LED_REQ_FAILS 自报fail=$(grep -cE '^CASE\|[^|]*\|FAIL\|' "$OUT") proc_rc=$prc")
        remember 1
    fi
else
    note "台账合同不成立 => 基础设施故障"
    MATRIX+=("normal | INFRA-FAIL 台账合同不成立 proc_rc=$prc")
    remember 4
fi

# ---------- 反向验证(3 项具名家族) ----------
inject_python() {           # inject_python <file> <old> <new> <marker>
    "$PY" - "$1" "$2" "$3" "$4" <<'PYEOF'
import io, sys
path, old, new, marker = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
t = io.open(path, encoding='utf-8', newline='').read()
n = t.count(old)
if n != 1:
    sys.stderr.write('ANCHOR-COUNT=%d (必须恰好 1 次)\n' % n)
    sys.exit(3)
io.open(path, 'w', encoding='utf-8', newline='').write(t.replace(old, new, 1))
sys.stderr.write('INJECTED %s\n' % marker)
PYEOF
}

reverse_case() {            # reverse_case <name> <target-file> <old> <new> <期望失败 id...>
    local name="$1" target="$2" old_txt="$3" new_txt="$4"; shift 4
    local ids="$*" work exe brc rc hit=0 miss=0 id extras="" nextra=0 r

    work="$OUT_DIR/reverse/$name"
    mkdir -p "$work" || { note "  $name: 无法建副本目录"; remember 4; return; }
    cp -r "$SRC_APP" "$work/app" || { note "  $name: 源码副本失败"; remember 4; return; }
    if ! inject_python "$work/app/$target" "$old_txt" "$new_txt" "$name"; then
        note "  $name: 注入未落地或锚点不唯一 => 基础设施故障, 不算检出"
        MATRIX+=("$name | INFRA-FAIL 注入未落地/锚点不唯一"); remember 4; return
    fi
    if ! grep -q "NEGATIVE-INJECTION" "$work/app/$target"; then
        note "  $name: 副本缺少注入标记"; MATRIX+=("$name | INFRA-FAIL 无注入标记"); remember 4; return
    fi
    exe="$work/rel.exe"; out="$work/out.txt"
    "$GCC" $FLAGS -I "$SHIM_DIR" -I "$work/app" "$REL_MAIN" -o "$exe" > "$work/build.txt" 2>&1
    brc=$?
    if [ "$brc" -ne 0 ]; then
        tail -15 "$work/build.txt"
        note "  $name: 副本编译失败 rc=$brc => 不算检出"
        MATRIX+=("$name | INFRA-FAIL 副本编译失败 rc=$brc"); remember 4; return
    fi
    "$exe" > "$out" 2>&1
    rc=$?
    EXECUTED+=("reverse:$name")
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
        note "  $name: rc=$rc 不是合同的业务失败 1 => 基础设施故障"
        MATRIX+=("$name | INFRA-FAIL 非合同退出码 rc=$rc"); remember 4; return
    fi
    if [ "$rc" -eq 0 ]; then
        note "  $name: 注入后全链仍通过 => 没检出"
        MATRIX+=("$name | NOT-DETECTED 注入后 proc_rc=0"); remember 1; return
    fi
    if ! validate_ledger "$out" "$ALL_CASES"; then
        note "  $name: 台账不完整/不自洽 => 基础设施故障"
        MATRIX+=("$name | INFRA-FAIL 台账合同不成立 rc=$rc"); remember 4; return
    fi
    for id in $ids; do
        if grep -q "^CASE|$id|FAIL|" "$out"; then hit=$((hit+1)); note "  命中期望失败身份: $id"
        else miss=$((miss+1)); note "  未命中期望失败身份: $id"; fi
    done
    if [ "$miss" -ne 0 ]; then
        MATRIX+=("$name | NOT-DETECTED 命中=$hit 缺失=$miss proc_rc=$rc"); remember 1; return
    fi
    for r in $ALL_CASES; do
        if grep -q "^CASE|$r|FAIL|" "$out"; then
            case " $ids " in *" $r "*) : ;; *) extras="$extras $r"; nextra=$((nextra+1)) ;; esac
        fi
    done
    [ "$nextra" -ne 0 ] && note "  额外失败身份(登记但不改变判定):$extras"
    MATRIX+=("$name | META-OK 命中 $hit 个失败身份 (实际FAIL行=$LED_FAILN 额外=$nextra proc_rc=$rc)")
}

if [ "$REVERSE" = "1" ]; then
    note ""
    note "=== 反向验证(10 组具名家族, 仓外一次性副本) ==="
    # RV1: 去掉"SG 必须是新独立样本"的身份门 —— 旧样本必须被 REL08 检出
    reverse_case RV1_sg_new_sample_identity_gate diag_relative.c \
'    fresh_sg  = (in->sg_valid  && in->sg_seq  != 0 && in->sg_seq  != e->last_sg_seq);' \
'    fresh_sg  = (in->sg_valid  != 0);   /* NEGATIVE-INJECTION RV1: 独立样本身份门被移除 */' \
        REL08-stale-sg-cannot-accumulate
    # RV2: 去掉 ADC 相对严重分支 —— 上偏/下偏与"自己的证据"两条都必须被检出
    reverse_case RV2_adc_relative_severe_branch diag_relative.c \
'    if (adc_strong_hit &&
        e->adc_strong_run_ms >= p->dur_adc_ms && e->adc_strong_cnt >= 2u)
    {
        e->verdict = REL_ADC_SEVERE; e->severe_active = 1;
        e->active_reason = (rt_uint8_t)e->verdict;
        return e->verdict;
    }' \
'    /* NEGATIVE-INJECTION RV2: ADC 相对严重分支被移除 */' \
        REL09-adc-severe-both-directions REL28-strong-needs-own-evidence
    # RV3: 去掉 RELATIVE 开启时的 profile 资格校验 —— 残缺 profile 必须被 REL16 检出
    reverse_case RV3_relative_mode_profile_qualification diagnosis.c \
'        else if (rel_profile_validate(&p, &why) != RT_EOK) rc = -RT_EINVAL;' \
'        /* NEGATIVE-INJECTION RV3: RELATIVE 开启门禁的 profile 资格校验被移除 */
        else if (RT_FALSE) rc = -RT_EINVAL;' \
        REL16-relative-mode-arm-gate
    # RV4: 让严重判据重新借用 warn 级的时间与样本数(A1/A2 的病根) —— REL28 必须检出
    reverse_case RV4_severe_borrows_warn_duration diag_relative.c \
'    if (sg_strong_hit &&
        e->sg_strong_run_ms >= p->dur_sg_ms && e->sg_strong_cnt >= p->min_new_sg)' \
'    if (sg_strong_hit &&   /* NEGATIVE-INJECTION RV4: 严重判据借用 warn 级时长/样本数 */
        e->sg_run_ms >= p->dur_sg_ms && e->sg_new_cnt >= p->min_new_sg)' \
        REL28-strong-needs-own-evidence
    # RV5: 覆盖丢失退回"只跳过判据不保护"(B2/B3) —— 生产入口三例都必须检出
    reverse_case RV5_coverage_loss_protects_nothing diag_relative.c \
'        if (armed && e->cov_lost_ms >= REL_COVERAGE_LOST_MS &&
            (motion_trusted ? motion_active(in->motor_state) : 1))' \
'        if (RT_FALSE && armed && e->cov_lost_ms >= REL_COVERAGE_LOST_MS &&
            (motion_trusted ? motion_active(in->motor_state) : 1))  /* NEGATIVE-INJECTION RV5: 覆盖丢失不再降险 */' \
        REL22-coverage-loss-protects-first REL33-lock-fail-bounded-degrade \
        REL34-no-frame-still-supervises REL36-null-outparams-still-protects
    # RV6: 失败的重学保留旧 learn 凭据(C4) —— REL39 必须检出
    reverse_case RV6_failed_relearn_keeps_credential diag_relative.c \
'    cand = d_pending;                    /* 私有候选: 从已验证的零点接着攒 */
    d_pending_learn = RT_FALSE;          /* 先作废这次尝试的凭据(C4) */' \
'    cand = d_pending;   /* NEGATIVE-INJECTION RV6: 入口不再作废本次尝试的 learn 凭据 */' \
        REL39-zero-static-and-failed-relearn
    # RV7(R6 范围): 去掉碰撞的独立触发 —— REL44 必须检出
    reverse_case RV7_impact_independent_trip_removed diag_relative.c \
'            if (e->imp_cnt >= p->impact_new_min)
            {
                e->verdict = REL_IMPACT; e->severe_active = 1;
                e->active_reason = (rt_uint8_t)e->verdict;
                return e->verdict;           /* 独立触发, 不要求 SG/ADC 同时异常 */
            }' \
'            /* NEGATIVE-INJECTION RV7: 碰撞独立触发被移除 */' \
        REL44-impact-fresh-evidence-only
    # RV8(M8-R2 R2-A.1): 控制锁"当作拿到了" —— 去掉拒绝就是 R1 的静默旁路。
    # REL45/REL46 必须检出(取锁失败仍然写字段、未初始化仍然提锁、读侧交出半份快照)。
    reverse_case RV8_ctl_lock_silently_bypassed diag_relative.c \
'rt_err_t rel_ctl_take(void)
{
    if (!d_ctl_inited) return -RT_EINVAL;
    return (rt_mutex_take(&d_ctl_lock, REL_CTL_TAKE_MS) == RT_EOK) ? RT_EOK : -RT_EBUSY;
}' \
'/* NEGATIVE-INJECTION RV8: 控制锁被"当作拿到了"(R1 的无锁写 + 永久旁路) */
rt_err_t rel_ctl_take(void) { return RT_EOK; }' \
        REL45-ctl-take-failure-writes-nothing REL46-ctl-uninit-take-and-protect
    # RV9(M8-R2 R2-B2): 新一代不再清旧判据证据 —— REL48 必须检出"新一代第一帧就触发"。
    reverse_case RV9_new_generation_keeps_old_evidence diag_relative.c \
'    if (need_wipe) rel_engine_new_generation(&d_rel_prod, g);' \
'    (void)need_wipe;   /* NEGATIVE-INJECTION RV9: 新一代不再清旧证据 */' \
        REL48-new-generation-clears-evidence
    # RV10(M8-R3 R3-A): **只**把"年龄"从运动事实可信判据里摘掉(覆盖保护本身还在) ——
    # REL50 的过期 IDLE/无效年龄/锁失败档三条不豁免断言与 REL49 的过期支必须检出。
    reverse_case RV10_motion_age_dropped_from_trust diag_relative.c \
'    motion_trusted = (in->motion_known &&
                     diag_rel_tick_diff(now, in->motion_tick) <= motion_age_limit);' \
'    motion_trusted = in->motion_known;   /* NEGATIVE-INJECTION RV10: 过期事实仍被当现在的静止 */' \
        REL50-stale-idle-not-static-exemption REL49-frame-not-same-as-motion-known
fi

note ""
note "=== 汇总 ==="
for m in ${MATRIX[@]+"${MATRIX[@]}"}; do note "  $m"; done
note "  执行集合: ${EXECUTED[*]}"
if [ "$SELECT_MODE" = "explicit" ]; then
    for id in $ALL_CASES; do
        case " ${SELECTED[*]} " in *" $id "*) : ;; *) note "  NOT_RUN $id" ;; esac
    done
fi
note "  声明用例数=$(printf '%s\n' ${SELECTED[@]} | grep -c .) 执行用例数=$(grep -cE '^CASE\|' "$OUT" 2>/dev/null || echo 0)"
if [ "$FINAL_RC" -eq 0 ]; then
    echo "M8 RELATIVE RESULT: PASS(主机链, 离线; 现场项未验证)"
else
    echo "M8 RELATIVE RESULT: FAIL rc=$FINAL_RC"
fi
exit "$FINAL_RC"
