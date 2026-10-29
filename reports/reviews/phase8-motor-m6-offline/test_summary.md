# M6 offline test summary

| 链 | 结果 |
|---|---|
| S1 target | 58/58 required PASS, rc=0 |
| S1 host_ovr | 73/73 required PASS, rc=0 |
| S1 reverse | rev_motor_off 5/5, rev_safety_trip_skip_post 1/1; rc=0 |
| I/O off_target/on_target/off_ovr/on_ovr | 33/35/46/48 required PASS, rc=0 |
| CLI strict target/host_ovr | 9/9 each PASS, rc=0 |
| TMC selftest identity | IO11a-f PASS in all four I/O variants |
| Field candidate selftest | M6D01-M6D06 6/6 PASS; pyserial not imported |
| M5 raw recovery | 19/19 b64 decode length/SHA equal; 3 flash batches rebuild CSV byte-equal (SHA c26cb75…) |

Notes:
- S2 not rerun: no stop_priority/HAL/persistence change; safety_trip command ordering covered by S1.
- V1/V2 not rerun: safety_state.c is stubbed there; no tmc2209/motor semantic change.
- Host GCC diagnostics are listed separately from Keil 0/0; FinSH generic function-pointer warnings are not claimed as host zero-warning.
