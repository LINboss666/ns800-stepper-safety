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
| Field candidate selftest | M6D01-M6D31 32/32 PASS (worktree and Git blob); pyserial not imported |
| M5 raw recovery | 19/19 b64 decode length/SHA equal; 3 flash batches rebuild CSV byte-equal (SHA c26cb75…) |

Notes:
- Candidate identity (Git blob SHA-256 and worktree CRLF SHA-256, both 32/32) is recorded
  in `reports/agent_handoffs/phase8-motor-m6-offline.json`; the two logs are
  `field_candidate_selftest.txt` (worktree) and `field_candidate_selftest_git_blob.txt`
  (committed blob bytes).
- S2 not rerun: no stop_priority/HAL/persistence change; safety_trip command ordering covered by S1.
- V1/V2 not rerun: safety_state.c is stubbed there; no tmc2209/motor semantic change.
- Independent reviews rejected the old candidate for D-1/D-2/D-3 and then for the evidence
  failure control flow. The current candidate fixes:
  - D-1: `Session`, `FDevice` and `FakeSerial` share one `FClock`; `SerialLink` sets the
    write timeout from the remaining budget and recomputes the read timeout before every
    read. M6D13/D14/D15 prove the time is charged to the same deadline; late cleanup is recorded.
  - D-2: `disarm_once` records transport/`OSError` cleanup failures separately; `run_fault`
    keeps the primary exception and traceback; an unconfirmed disable is UNKNOWN. M6D16-D18,
    M6D23, M6D30.
  - D-3: arm is confirmed only by one complete `motor_status` snapshot
    (`state=0 armed=1 cur=0 tgt=0`); refusal text, armed=0, missing/duplicate fields and
    conflicting state cannot send target/start. M6D19-M6D22.
  - D-2e: a normal business `q()` now raises `EvidenceError` immediately after a raw-save
    failure. Cleanup-only `motor_disarm`/`safety_status` queries are exempt so the disable is
    still attempted and can be confirmed; the overall run remains FAIL. Evidence failure
    before arm sends no motor command; after arm/start it triggers exactly one cleanup and no
    trip/recovery; a disarm, verification or recovery evidence failure stops before
    `fault_reset`/`system_selftest` even with `--allow-recovery`. M6D24-M6D31.
- The original 25 case identities are retained; M6D18/M6D24 were tightened to the evidence
  gate behavior and M6D25-M6D31 add the evidence seams. The earlier first-failure
  transcription (`field_candidate_selftest_first_failure.txt`, 20/24) and the D-2e first-failure
  raw log (`field_candidate_selftest_first_failure_d2e.txt`, 23/25 with M6D18/M6D24 failing)
  are both retained; the first is explicitly a transcription, the second is the unmodified
  console log.
- PS18h injects `-RT_EIO`; the host shim defines `RT_EIO=12`, so the captured line shows
  `rc=-12`. The assertion is symbolic; the target's errno value is not asserted here.
- The field candidate requires an accepted M5 `archive_state.json` (and a verified second
  copy after a clear) before importing pyserial. It remains
  FIELD_CANDIDATE_OFFLINE_FIXED / NOT_BOARD_EXECUTED and is not approved for a field run.
- S1/I/O/CLI/S2 were not rerun for the script-only fixes; Keil was not rebuilt. The original
  three build stdout files are archived under `reports/reviews/phase8-motor-m6-offline/`
  (force-added because `*.log` is ignored); no build was repeated. Their Program Size lines
  retain the original trailing whitespace; `git diff --check` therefore reports those three
  legacy lines, not a new source defect.
- Host GCC diagnostics are listed separately from Keil 0/0; FinSH generic function-pointer
  warnings are not claimed as host zero-warning.
