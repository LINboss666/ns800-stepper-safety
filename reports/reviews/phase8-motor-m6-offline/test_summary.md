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
| Field candidate selftest | M6D01-M6D10 11/11 PASS; pyserial not imported |
| M5 raw recovery | 19/19 b64 decode length/SHA equal; 3 flash batches rebuild CSV byte-equal (SHA c26cb75…) |

Notes:
- S2 not rerun: no stop_priority/HAL/persistence change; safety_trip command ordering covered by S1.
- V1/V2 not rerun: safety_state.c is stubbed there; no tmc2209/motor semantic change.
- PS18h injects `-RT_EIO`; the host shim defines `RT_EIO=12`, so the captured line shows
  `rc=-12`. The assertion is symbolic; the target's errno value is not asserted here.
- The field candidate now performs a full `flash_dump` readback (slot coverage + session +
  event + time), preserves every raw RX/TX in `--out-dir`, and forbids recovery unless a
  confirmed LOW/pad=0 disarm and a complete 300-row readback both pass. It remains
  OFFLINE_VERIFIED / NOT_BOARD_EXECUTED.
- Host GCC diagnostics are listed separately from Keil 0/0; FinSH generic function-pointer
  warnings are not claimed as host zero-warning.
