/* Independent continuation of ARD4: actual product C via archived host TU. */
#define main archived_main
#include REVIEW_HOST_MAIN
#undef main

int main(void)
{
    diag_rprof_t cfg_before, cfg_after, pend_before;
    int cfg_base, cfg_steps, refused, cfg_changed, pend_changed, creds_kept;
    int zb, lb;

    sensor_service_sg_startup_release();
    prep_zero_cruise();
    if (learn_run(400) != REL_JOB_DONE || !d_pending_learn) return 4;
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;

    cfg_base = g_cfg_take_calls;
    cap_reset();
    do_cmd(cmd_commit);
    cfg_steps = g_cfg_take_calls - cfg_base;
    if (!cap_has("commit OK") || cfg_steps < 1) return 4;
    if (project_config_get_rprof(&cfg_before) != RT_EOK) return 4;
    pend_before = d_pending;
    zb = d_pending_zero; lb = d_pending_learn;

    /* The successful call calibrates the cfg publication point. Let ctx
     * gate/read/read/publication succeed, then fail only rollback acquisition.
     * Both failures use the existing archived harness injection facilities. */
    g_cfg_take_calls = 0;
    g_cfg_take_fail_after = cfg_steps - 1;
    g_ctx_take_calls = 0;
    g_ctx_take_fail_after = 4;
    cap_reset();
    do_cmd(cmd_commit);
    g_cfg_take_fail_after = -1;
    g_ctx_take_fail_after = -1;
    if (project_config_get_rprof(&cfg_after) != RT_EOK) return 4;

    refused = cap_has("commit REFUSED");
    cfg_changed = memcmp(&cfg_before, &cfg_after, sizeof(cfg_before)) != 0;
    pend_changed = memcmp(&pend_before, &d_pending, sizeof(pend_before)) != 0;
    creds_kept = d_pending_zero == zb && d_pending_learn == lb;
    printf("OBS|COMMIT_CFG_AND_ROLLBACK_FAULT|refused=%d no_rollback=%d cfg_changed=%d pending_changed=%d creds_kept=%d cfg_id=%u pending_id=%u ctx_takes=%d cfg_steps=%d\n",
           refused, cap_has("config-write-no-rollback"), cfg_changed, pend_changed,
           creds_kept, (unsigned)cfg_after.profile_id, (unsigned)d_pending.profile_id,
           g_ctx_take_calls, cfg_steps);
    printf("REVIEW|REFUSED_COMMIT_PRESERVES_ALL_STATE_ON_ROLLBACK_LOCK_FAILURE|%s\n",
           refused && !cfg_changed && !pend_changed && creds_kept ? "PASS" : "FAIL");
    return refused && !cfg_changed && !pend_changed && creds_kept ? 0 : 1;
}
