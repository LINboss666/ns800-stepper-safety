#define main archived_main
#include REVIEW_HOST_MAIN
#undef main

int main(void)
{
    diag_rprof_t before;
    sensor_service_sg_startup_release();
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    if (!d_pending_zero) return 4;
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 500;
    pump_sensor(2);
    do_cmd(cmd_learn);
#ifdef REVIEW_ASYNC
    if (job_wait(400) != REL_JOB_DONE) return 4;
#endif
    if (!d_pending_learn) return 4;
    before = d_pending;
    cap_reset();
    do_cmd(cmd_clear);
    printf("CLEAR_CONTROL|refused=%d|pending_changed=%d|zero_cred=%d|learn_cred=%d\n",
           cap_has("clear REFUSED: static txn"), memcmp(&before,&d_pending,sizeof(before)) != 0,
           (int)d_pending_zero,(int)d_pending_learn);
    return (cap_has("clear REFUSED: static txn") && d_pending_zero && d_pending_learn &&
            memcmp(&before,&d_pending,sizeof(before)) == 0) ? 0 : 1;
}
