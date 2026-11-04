#define main archived_main
#include REVIEW_HOST_MAIN
#undef main

static void frame(const char *name, void (*fn)(void *))
{
    cap_reset();
    do_cmd(fn);
    printf("FRAME_BEGIN|%s\n", name);
    fputs(cap_text(), stdout);
    printf("FRAME_END|%s\n", name);
}

int main(void)
{
    sensor_service_sg_startup_release();
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    frame("job_initial", cmd_learn_status);
    frame("zero", cmd_zero);
    if (!d_pending_zero) return 4;
    frame("job_zero", cmd_learn_status);
    frame("learn_start", cmd_learn);
    if (!cap_has("learn STARTED id=1")) return 4;
    pump(20);
    frame("job_failed", cmd_learn_status);
    printf("HOST_STATE|id=%u state=%u owner=%u zero=%d learn=%d active=%u\n",
           (unsigned)d_job.job_id, (unsigned)d_job.state, (unsigned)d_cap_owner,
           (int)d_pending_zero, (int)d_pending_learn, (unsigned)d_cap.active);
    return d_job.job_id == 1 && d_job.state == REL_JOB_FAILED &&
           d_cap_owner == REL_CAP_FREE && d_pending_zero &&
           !d_pending_learn && !d_cap.active ? 0 : 4;
}
