/* Independent reviewer probes. Product sources are unmodified Git blobs.
 * Reuse the delivered deterministic C harness; no devices/OS serial APIs.
 * OBSERVED_DEFECT is an observation, not a regression PASS. */
#define main m8_delivered_main
#include "tree/tests/host/rel_main.c"
#undef main

static int found;
#define OBS(id,cond,fmt,...) do { int bad=!!(cond); found+=bad; \
  printf("REVIEW|%s|%s|" fmt "\n",id,bad?"OBSERVED_DEFECT":"NOT_OBSERVED",##__VA_ARGS__); }while(0)

static void cov_hook(int n) { if(n==10) S.cfg_state=TMC_DRV_UNCONFIGURED; }
static void lock_hook(int n) { if(n==10) g_mutex_take_fail=1; }
static void zero_hook(int n) { if(n==5) { S.mstate=MOTOR_CRUISE;S.armed=1;S.mhz=500;S.raw=3500; } }

int main(void) {
    rel_engine_t e; struct feed f; diag_rprof_t p,back;
    rel_reason_t rr=REL_REASON_NONE; int i,pre,rc;

    world_reset();p=make_profile();feed_init(&e,&p,&f);
    for(i=1;i<=60;i++) feed_step(&f,447,2000,i%10==0,1,1,1);
    pre=e.sg_run_ms;rr=feed_step(&f,383,2000,1,1,1,1);
    OBS("A1-SG-warn-time-counted-as-severe",rr==REL_SG_SEVERE,
        "warn_ms=%d first_strong_sample_reason=%s span=%u",pre,rel_reason_name(rr),e.sg_run_ms);

    world_reset();p=make_profile();feed_init(&e,&p,&f);
    for(i=1;i<=40;i++) feed_step(&f,500,2123,i%10==0,1,1,1);
    pre=e.adc_run_ms;rr=feed_step(&f,500,2187,0,1,1,1);
    OBS("A2-ADC-warn-time-counted-as-severe",rr==REL_ADC_SEVERE,
        "warn_ms=%d first_strong_sample_reason=%s span=%u",pre,rel_reason_name(rr),e.adc_run_ms);

    world_reset();p=make_profile();feed_init(&e,&p,&f);
    feed_step(&f,500,2000,1,1,1,1);
    for(i=1;i<=120;i++)rr=feed_step(&f,500,2000,i%10==0,0,1,1);
    OBS("B1-frozen-ADC-identity-is-not-expired",rr!=REL_SENSOR_UNAVAILABLE,
        "cur_age=%u frame_age=%u reason=%s",f.a.now_tick-f.a.in.cur_tick,
        f.a.now_tick-f.a.in.timestamp,rel_reason_name(rr));

    world_reset();p=make_profile();install_profile(&p);d_mode=DIAG_MODE_RELATIVE_PROTECTION;
    g_tick_hook=cov_hook;pump(100);g_tick_hook=RT_NULL;
    OBS("B2-production-signature-loss-skips-protection",g_post_calls==0,
        "post_calls=%d mode=%d mismatch_text=%d",g_post_calls,d_mode,cap_has("sig-mismatch"));

    world_reset();p=make_profile();install_profile(&p);d_mode=DIAG_MODE_RELATIVE_PROTECTION;
    g_tick_hook=lock_hook;pump(100);g_tick_hook=RT_NULL;
    OBS("B3-production-lock-failure-skips-protection",g_post_calls==0,
        "post_calls=%d mode=%d lock_fail_text=%d",g_post_calls,d_mode,cap_has("config-lock"));

    world_reset();p=make_profile();install_profile(&p);d_mode=DIAG_MODE_RELATIVE_PROTECTION;
    rc=diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    OBS("C1-mode-can-be-disabled-in-motion",rc==RT_EOK&&d_mode==DIAG_MODE_MONITOR_ONLY,
        "rc=%d moving_state=%d armed=%d",rc,S.mstate,S.armed);

    world_reset();p=make_profile();install_profile(&p);S.mstate=MOTOR_IDLE;S.armed=0;S.mhz=0;
    g_static_begin_busy=1;diagnosis_take_relative_confirm();rc=diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    OBS("C2-enable-ignores-static-transaction-refusal",rc==RT_EOK,
        "rc=%d static_begin_calls=%d mode=%d",rc,g_static_begin_calls,d_mode);

    world_reset();g_mutex_take_fail=1;rc=project_config_save();
    OBS("C3-config-save-writes-after-lock-failure",rc==RT_EOK&&g_blob_valid,
        "rc=%d saved=%d releases=%d",rc,g_blob_valid,g_mutex_release_calls);

    world_reset();S.mstate=MOTOR_IDLE;S.armed=0;S.mhz=0;S.raw=1660;do_cmd(cmd_zero);
    S.mstate=MOTOR_CRUISE;S.armed=1;S.mhz=500;S.raw=2000;pump_sensor(2);do_cmd(cmd_learn);
    { char *av[]={"diag_profile","learn","8","600","700","1"};
      diag_profile(6,av); }
    pre=d_pending_learn;S.mstate=MOTOR_IDLE;S.armed=0;S.mhz=0;do_cmd(cmd_commit);
    memset(&back,0,sizeof(back));project_config_get_rprof(&back);
    OBS("C4-failed-relearn-reuses-old-learn-credential",pre&&back.hz_low==600,
        "learn_credential=%d published_window=%u..%u dir_mask=%u",pre,back.hz_low,back.hz_high,back.dir_mask);

    world_reset();S.mstate=MOTOR_IDLE;S.armed=0;S.mhz=0;S.raw=1660;
    g_pump_hook=zero_hook;do_cmd(cmd_zero);g_pump_hook=RT_NULL;
    OBS("C5-zero-accepts-motion-during-capture",d_pending_zero&&S.armed,
        "zero_accepted=%d final_armed=%d zero_band=%u..%u",d_pending_zero,S.armed,d_pending.zero_min,d_pending.zero_max);

    world_reset();S.mstate=MOTOR_IDLE;S.armed=0;S.mhz=0;S.raw=1660;S.sg=64;do_cmd(cmd_zero);
    S.mstate=MOTOR_CRUISE;S.armed=1;S.mhz=500;S.raw=2000;S.sg=64;pump_sensor(2);do_cmd(cmd_learn);
    S.mstate=MOTOR_IDLE;S.armed=0;S.mhz=0;cap_reset();do_cmd(cmd_commit);
    OBS("D1-low-SG-baseline-fixed-margin-is-uncommittable",cap_has("SG_TRIP_UNREACHABLE"),
        "normal_sg_min=%u generated_drop=%u unreachable=%d",d_pending.run_sg_min,d_pending.sg_drop_trip,cap_has("SG_TRIP_UNREACHABLE"));

    world_reset();p=make_profile();p.profile_id=0xFFFFFFFFu;p.revision=0xFFFFFFFFu;
    install_profile(&p);d_mode=DIAG_MODE_RELATIVE_PROTECTION;
    s_prev.sg_seq=4000000000u;s_prev.cur_seq=4000000000u;
    S.raw=2191;S.sg=500;pump(100);diag_relative_status_lines();
    OBS("E1-critical-relative-lines-exceed-console-cap",cap_max_line_len()>126,
        "max_line_bytes=%d",cap_max_line_len());

    world_reset();p=make_profile();install_profile(&p);d_mode=DIAG_MODE_RELATIVE_PROTECTION;
    S.raw=2191;pump(80);pre=g_post_calls;
    /* Model the documented fault-reset/selftest state progression; unchanged
     * safety_state.c has no callback changing d_mode or d_rel_prod. */
    S.safety=SAFETY_FAULT_LATCHED;S.mstate=MOTOR_FAULT;S.armed=0;S.mhz=0;pump(5);
    S.safety=SAFETY_READY;S.mstate=MOTOR_IDLE;S.armed=0;S.mhz=0;S.raw=2000;pump(60);
    S.mstate=MOTOR_CRUISE;S.armed=1;S.mhz=500;S.raw=2191;pump(100);
    OBS("C6-second-incident-silently-has-no-new-request",pre==1&&g_post_calls==1&&d_mode==DIAG_MODE_RELATIVE_PROTECTION,
        "first_posts=%d total_posts=%d mode=%d latched=%d",pre,g_post_calls,d_mode,d_rel_prod.latch.latched);

    printf("REVIEW_SUMMARY|observed=%d|hardware_actions=0|sources_unmodified=YES\n",found);
    return 0; /* This executable records observations, not a product PASS. */
}
