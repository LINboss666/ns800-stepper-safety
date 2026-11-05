#define main rel_harness_original_main
#include "rel_main.c"
#undef main

/* Host-only probe. Inputs are synthetic; the included production C is unchanged. */
int main(void)
{
    diag_rprof_t p;
    rel_engine_t e;
    struct feed f;
    rel_reason_t r = REL_REASON_NONE;
    unsigned seen = 0;
    int i, failures = 0;

    world_reset(); p = make_profile(); feed_init(&e, &p, &f);
    (void)feed_step(&f, 500, 2000, 1, 1, 1, 1); /* enter the valid window before a synthetic incident */
    ok_clear(&e);
    e.severe_active = 1; e.active_reason = REL_SG_SEVERE;
    e.latch.latched = 1; e.rearm_required = 1;
    for (i = 0; i < 70; ++i)
    {
        r = feed_step(&f, 500, 2000, 1, 1, 1, 1);
        seen |= e.ok_src_seen;
    }
    printf("OBS|two_source_recovery|seen=%u|imu_seq=%u|imu_usable=%u|severe=%u|latch=%u|rearm=%u|reason=%s\n",
           seen, f.a.in.imu_seq, e.imu_usable, e.severe_active,
           e.latch.latched, e.rearm_required, rel_reason_name(r));
    failures += !(seen == 3u && f.a.in.imu_seq == 0u && e.imu_usable == 0u &&
                  e.severe_active == 0u && e.latch.latched == 1u && e.rearm_required == 1u);

    world_reset(); p = make_profile(); feed_init(&e, &p, &f);
    (void)feed_step(&f, 500, 2000, 1, 1, 1, 1); /* establish the ADC sample identity once */
    ok_clear(&e);
    e.severe_active = 1; e.active_reason = REL_SG_SEVERE;
    seen = 0;
    for (i = 0; i < 70; ++i)
    {
        r = feed_step(&f, 500, 2000, 1, 0, 1, 1);
        seen |= e.ok_src_seen;
    }
    printf("OBS|single_source_not_recovered|seen=%u|severe=%u|reason=%s\n",
           seen, e.severe_active, rel_reason_name(r));
    failures += !(seen == 1u && e.severe_active == 1u);

    world_reset(); p = make_profile(); feed_init(&e, &p, &f);
    f.a.in.motor_state = MOTOR_ACCEL;
    for (i = 0; i < 2; ++i)
        r = feed_step_imu(&f, 500, 2000, 1, 1, 1, 1, 3000, 1);
    printf("OBS|ramp_impact_suppressed|reason=%s|impact_count=%u|severe=%u\n",
           rel_reason_name(r), e.imp_cnt, e.severe_active);
    failures += !(r == REL_REASON_NONE && e.imp_cnt == 0u && e.severe_active == 0u);

    world_reset(); p = make_profile(); feed_init(&e, &p, &f);
    for (i = 0; i < 2; ++i)
        r = feed_step_imu(&f, 500, 2000, 1, 1, 1, 1, 3000, 1);
    printf("OBS|cruise_impact_positive_control|reason=%s|impact_count=%u|severe=%u\n",
           rel_reason_name(r), e.imp_cnt, e.severe_active);
    failures += !(r == REL_IMPACT && e.imp_cnt == 2u && e.severe_active == 1u);

    printf("SUMMARY|cases=4|failures=%d|hardware_actions=0\n", failures);
    return failures ? 1 : 0;
}
