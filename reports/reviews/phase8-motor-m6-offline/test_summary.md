# M6 offline test summary

Final-source runs (product source unchanged since `cf133d6`):

| 链 | 结果 |
|---|---|
| S1 target | 58/58 required PASS, rc=0 |
| S1 host_ovr | 73/73 required PASS, rc=0 |
| S1 reverse | rev_motor_off 5/5, rev_safety_trip_skip_post 1/1; rc=0 |
| I/O off_target/on_target/off_ovr/on_ovr | 33/35/46/48 required PASS, rc=0 |
| CLI strict target/host_ovr | 10/10 each PASS, rc=0 |
| TMC selftest identity | IO11a-f PASS in all four I/O variants |
| Field candidate selftest | M6D01-M6D24 25/25 PASS; pyserial not imported |
| M5 raw recovery | 19/19 b64 decode length/SHA equal; 3 flash batches rebuild CSV byte-equal (SHA c26cb75…) |

Notes:
- S2 not rerun: no stop_priority/HAL/persistence change; safety_trip command ordering covered by S1.
- V1/V2 not rerun: safety_state.c is stubbed there; no tmc2209/motor semantic change.
- The independent review (2026-10-29) rejected the old candidate for three reasons; the
  rewritten candidate fixes them and was re-run only for the script/evidence set:
  - D-1: `Session`, `FDevice` and `FakeSerial` share one `FClock`; `SerialLink` sets the
    write timeout from the remaining budget and recomputes the read timeout before every
    read. M6D13 (10.1 s arm write block), M6D14 (start write timeout) and M6D15 (start read
    block) prove the time is charged to the same deadline; late cleanup is recorded.
  - D-2: `disarm_once` catches transport/`OSError` failures separately; `run_fault` keeps
    the primary exception and traceback, rejects a successful return after an evidence
    failure, and never reports an unconfirmed disable as success. M6D16/M6D18/M6D23/M6D24.
  - D-3: arm is confirmed only by one complete `motor_status` snapshot
    (`state=0 armed=1 cur=0 tgt=0`); refusal text, armed=0, missing/duplicate fields and
    conflicting state cannot send target/start. M6D19-M6D22.
- The original 13 case identities (M6D01, M6D02, M6D02b, M6D03-M6D12) are retained.
  The first failing run after the rewrite is preserved as
  `field_candidate_selftest_first_failure.txt` (c2/c2b/c7/c17 failed before the fixture
  fixes).
- PS18h injects `-RT_EIO`; the host shim defines `RT_EIO=12`, so the captured line shows
  `rc=-12`. The assertion is symbolic; the target's errno value is not asserted here.
- The field candidate now requires an accepted M5 `archive_state.json` (and a verified
  second copy after a clear) before importing pyserial. It remains
  FIELD_CANDIDATE_OFFLINE_FIXED / NOT_BOARD_EXECUTED and is not approved for a field run.
- S1/I/O/CLI were not rerun for the script-only D fixes; Keil was not rebuilt. The original
  three build stdout files are archived under `reports/reviews/phase8-motor-m6-offline/`
  (force-added because `*.log` is ignored); no build was repeated to recreate them.
- Host GCC diagnostics are listed separately from Keil 0/0; FinSH generic function-pointer
  warnings are not claimed as host zero-warning.
