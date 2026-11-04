/* Independent review probes. Uses the archived host environment and actual
 * production C translation units; no product implementation is copied here. */
#define main archived_main
#ifndef REVIEW_HOST_MAIN
#error "Define REVIEW_HOST_MAIN to the archived tests/host/rel_main.c path"
#endif
#include REVIEW_HOST_MAIN
#undef main

static int review_failures;
static void result(const char *id, int ok)
{
    printf("REVIEW|%s|%s\n", id, ok ? "PASS" : "FAIL");
    if (!ok) review_failures++;
}

int main(void)
{
    rt_err_t rc;
    int before_take, before_release;
    int owner_before, active_before;
    diag_rprof_t before, after, pending_before;
    int zero_before, learn_before;

    sensor_service_sg_startup_release(); /* same startup precondition as archived main */

    world_reset();
    before_take = g_ctx_take_calls;
    before_release = g_mutex_release_calls;
    rc = rel_job_status(RT_NULL);
    printf("OBS|NULL_STATUS|rc=%d ctx_held=%u takes=%d releases=%d\n",
           (int)rc, (unsigned)d_ctx_lock.held, g_ctx_take_calls-before_take,
           g_mutex_release_calls-before_release);
    result("NULL_STATUS_DOES_NOT_LEAK_LOCK", rc == -RT_EINVAL && d_ctx_lock.held == 0u);

    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    /* pending_read and collector_open take #1/#2 succeed; the pending
     * invalidation (#3) and cleanup acquisition (#4) time out. */
    g_ctx_take_fail_after = 2;
    do_cmd(cmd_zero);
    owner_before = d_cap_owner;
    active_before = d_cap.active;
    printf("OBS|ZERO_FAILED_EXIT|owner=%d active=%d ctx_takes=%d\n",
           owner_before, active_before, g_ctx_take_calls);
    g_ctx_take_fail_after = -1; /* the transient lock fault is now gone */
    pump(3); /* permit bounded deferred cleanup; original code has none */
    cap_reset();
    do_cmd(cmd_zero);
    printf("OBS|ZERO_RETRY_AFTER_FAULT_CLEARED|owner=%u active=%u zero_cred=%d busy=%d\n",
           (unsigned)d_cap_owner, (unsigned)d_cap.active, (int)d_pending_zero,
           cap_has("zero REFUSED: capture busy"));
    result("FAILED_ZERO_RECOVERS_COLLECTOR_AFTER_LOCK_FAULT",
           d_cap_owner == (rt_uint8_t)REL_CAP_FREE && d_cap.active == 0u && d_pending_zero);

    prep_zero_cruise();
    if (learn_run(400) != REL_JOB_DONE || !d_pending_learn)
    { puts("PRE|qualified_candidate_failed"); return 4; }
    before = d_pending;
    cap_reset();
    do_cmd(cmd_clear); /* still armed and cruising: static transaction MUST refuse */
    printf("OBS|CLEAR_STATIC_REFUSED|refused=%d zero_cred=%d learn_cred=%d pending_changed=%d\n",
           cap_has("clear REFUSED: static txn"), (int)d_pending_zero,
           (int)d_pending_learn, memcmp(&before, &d_pending, sizeof(before)) != 0);
    result("REFUSED_CLEAR_PRESERVES_PENDING_AND_CREDENTIALS",
           cap_has("clear REFUSED: static txn") && d_pending_zero && d_pending_learn &&
           memcmp(&before, &d_pending, sizeof(before)) == 0);

    prep_zero_cruise();
    if (learn_run(400) != REL_JOB_DONE || !d_pending_learn)
    { puts("PRE|qualified_candidate_failed"); return 4; }
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    if (project_config_get_rprof(&before) != RT_EOK) return 4;
    pending_before = d_pending;
    zero_before = d_pending_zero; learn_before = d_pending_learn;
    g_ctx_take_calls = 0;
    /* gate #1, initial pending snapshot #2 and final snapshot #3 pass;
     * project_config_set_rprof succeeds; post-write pending publication #4 fails. */
    g_ctx_take_fail_after = 3;
    cap_reset();
    do_cmd(cmd_commit);
    g_ctx_take_fail_after = -1;
    if (project_config_get_rprof(&after) != RT_EOK) return 4;
    printf("OBS|COMMIT_POSTWRITE_CTX_FAULT|refused=%d cfg_before_magic=%08X cfg_after_magic=%08X cfg_changed=%d ctx_takes=%d\n",
           cap_has("commit REFUSED"), (unsigned)before.magic, (unsigned)after.magic,
           memcmp(&before, &after, sizeof(before)) != 0, g_ctx_take_calls);
    /* Do not require a future fix to retain this exact acquisition order.
     * A fully coherent success is valid too; refused-but-applied is not. */
    result("COMMIT_OUTCOME_MATCHES_COMPLETE_TRANSACTION",
           (cap_has("commit REFUSED") && memcmp(&before, &after, sizeof(before)) == 0 &&
            memcmp(&pending_before, &d_pending, sizeof(pending_before)) == 0 &&
            d_pending_zero == zero_before && d_pending_learn == learn_before) ||
           (cap_has("commit OK") && after.magic == DIAG_RPROF_MAGIC &&
            d_pending_zero && d_pending_learn &&
            memcmp(&after, &d_pending, sizeof(after)) == 0));

    printf("REVIEW_SUMMARY|checks=4|failures=%d|hardware_actions=0\n", review_failures);
    return review_failures ? 1 : 0;
}
