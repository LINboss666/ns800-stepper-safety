import json, hashlib, subprocess
from pathlib import Path

def sh(b): return hashlib.sha256(b).hexdigest()
def blob(rev, p): return subprocess.check_output(['git', 'cat-file', 'blob', rev + ':' + p], cwd=r'D:\ns800')

COL = 'reports/reviews/phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py'
VER = 'reports/reviews/phase8-motor-m7-current-calibration-r2/verify_p0_candidate.py'
CAL = 'reports/reviews/phase8-motor-m7-current-calibration/m7_calcalc.py'
BIN = 'reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.bin'
AXF = 'reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.axf'
HEAD = '8bade29677dd3f2395cfd92ce28b9cd7206c89e2'
PIN = '24ab5a7c1cff259b209f78fcb1abbb5f48271581'
pkg = Path('C:/Users/LMX/Downloads/p0_settings_r2_unzip')
SP = Path('C:/Users/LMX/AppData/Local/Programs/Python/Python312/Lib/site-packages/serial')
patch = (pkg / 'candidate/port_settings_r2.patch').read_bytes()

d = {
 "task": "phase8-motor-m7-p0-serial-settings-r2",
 "package_zip": {"bytes": 85793, "sha256": "36877503dc120ebf3e810a15b24d726e0e656a5f2ee2e9d386e509c4db8c29c0"},
 "package_manifest_verified": "33/33 entries OK (sha256sum -c SHA256SUMS.txt)",
 "baseline": {
   "local_head": HEAD,
   "remote_tip_debug_branch": HEAD,
   "reviewed_against_commit": "280ca815c89419184a457fd6334942c9b543d5b1",
   "delta_280ca81_to_HEAD": "18 files, all under reports/ (evidence + handoff of 4fbb11e, e93971d, b3c07a3, 8bade29)",
   "product_test_firmware_delta": 0,
   "working_tree_before_apply": "clean",
   "frozen_refs_readonly": {
     "main": "1b3757eec550802a3973e66790af422d81d3d373",
     "phase7/full-embedded-software": "6d3fbc559d45673d7550eb73dec5a4ade5bb34df",
     "phase8/tmc-drive-config": "1696c59f4b56bae5d503fb18888e379dfad6f76f",
     "tag v0.7-phase7-software-board-validated": "097935fec5a6f2467261ab1ad449ff0b1d398250 -> e40a9b95ef709e6a80444f9f3949dfdd70ba96ba"}},
 "patch": {
   "path": "candidate/port_settings_r2.patch", "bytes": len(patch), "sha256": sh(patch),
   "expected_sha256": "c5793e3578d8455f8b98c68825c800f445ec1897cbb44d4b001d8ac7b30caa15",
   "method": "canonical HEAD blobs exported to D:/p0r2_work/apply (repo-external), git -c core.autocrlf=false apply --check then apply",
   "apply_check_rc": 0, "apply_rc": 0,
   "files_touched": [COL, VER], "other_files_created_by_patch": 0},
 "collector": {
   "pre_apply_blob_bytes": 23374, "pre_apply_blob_sha256": sh(blob(HEAD, COL)),
   "post_apply_bytes": len(Path(COL).read_bytes()), "post_apply_sha256": sh(Path(COL).read_bytes()),
   "expected": "c98bb712bd454dd66ffabece49b8998075642b5cca4d07c85073a3508d90a726",
   "cr_bytes_in_working_copy": 0,
   "byte_identical_to_packaged_candidate": Path(COL).read_bytes() == (pkg / 'candidate/p0_voltage_observation.py').read_bytes()},
 "verifier": {
   "pre_apply_blob_bytes": 21742, "pre_apply_blob_sha256": sh(blob(HEAD, VER)),
   "post_apply_bytes": len(Path(VER).read_bytes()), "post_apply_sha256": sh(Path(VER).read_bytes()),
   "expected": "99a449f6d80ee2999916b34acfaf6aa5f462f40b1bd7bdd871a4aba366ff8a45",
   "cr_bytes_in_working_copy": 0,
   "byte_identical_to_packaged_candidate": Path(VER).read_bytes() == (pkg / 'candidate/verify_p0_candidate.py').read_bytes()},
 "unchanged_inputs": {
   "calculator": {"bytes": len(Path(CAL).read_bytes()), "sha256": sh(Path(CAL).read_bytes()),
     "expected": "5c7a2340b4d350b1e8932798a57b85e8a6f4818501e469ad6dfd5c1bd29163ad"},
   "payload_bin": {"bytes": len(Path(BIN).read_bytes()), "sha256": sh(Path(BIN).read_bytes()),
     "expected": "e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da"},
   "payload_axf": {"bytes": len(Path(AXF).read_bytes()), "sha256": sh(Path(AXF).read_bytes()),
     "expected": "2205c7e7ad6bad93e848fd24d1e5f38c76796865a2b21ebcd2d4e22bb4b6d584"},
   "firmware_input_commit": "5b564eb962e8518920f581111d7a460a2b1fce64",
   "c_h_hal_kernel_macros_projects_images_changed": 0, "keil_build": 0, "flash": 0},
 "proof_inputs_pinned_to_24ab5a7": {
   "board-proof.json": {"bytes": len(blob(PIN, 'reports/agent_handoffs/phase8-motor-m7-p0-board.json')),
     "sha256": sh(blob(PIN, 'reports/agent_handoffs/phase8-motor-m7-p0-board.json')),
     "expected": "e8df121f3f58af5fadf3d60be24fd8aaf7d88c381ae740be86173c43e6604246"},
   "boot.b64": {"bytes": len(blob(PIN, 'reports/reviews/phase8-motor-m7-p0-board/S3_raw_boot.bin.b64')),
     "sha256": sh(blob(PIN, 'reports/reviews/phase8-motor-m7-p0-board/S3_raw_boot.bin.b64')),
     "expected": "46b53e93137a7a4f90ecd16ea73bf70435bc14514e323032d04d0665c478dd46"},
   "decoded_boot": {"bytes": 3381, "sha256": "bc47dc022b25bd483c16ab749895bf2a9d7dba318fdf88826357d2dbb98214fa"},
   "HEAD_version_deliberately_not_used": {
     "reason": "task section 6.3 pins the proof to fixed 24ab5a7 objects; the later errata-updated board.json would change the pinned digest",
     "sha256_of_HEAD_blob": sh(blob(HEAD, 'reports/agent_handoffs/phase8-motor-m7-p0-board.json'))}},
 "host_environment": {
   "python": "3.12.5 (tags/v3.12.5:ff3bc82, Aug 6 2024) MSC v.1940 64 bit (AMD64)",
   "python_executable": r"C:\Users\LMX\AppData\Local\Programs\Python\Python312\python.exe",
   "installed_pyserial_version": "3.5",
   "installed_pyserial_path": str(SP),
   "default_text_encoding": "cp936 (GBK), locale.getpreferredencoding(False)",
   "packaged_upstream_v35_serialutil_matches_installed": sh((pkg / 'pyserial35/serialutil.py').read_bytes()) == sh((SP / 'serialutil.py').read_bytes()),
   "packaged_upstream_v35_serialwin32_matches_installed": sh((pkg / 'pyserial35/serialwin32.py').read_bytes()) == sh((SP / 'serialwin32.py').read_bytes()),
   "network_or_install_used": 0},
 "offline_results": {
   "selftest37_native": {"rc": 0, "stdout": "selftest37_windows.stdout.txt", "equals_cloud": "results/retained37.txt after CR normalisation"},
   "selftest11_native": {"rc": 0, "stdout": "selftest11_windows.stdout.txt", "equals_cloud": "results/retained11.txt after CR normalisation"},
   "static9_native": {"rc": 1, "pass": 8, "fail": 1,
     "failed_case": "P0C09-one-open-time-config-and-zero-setting-stores",
     "error": "UnicodeDecodeError: 'gbk' codec can't decode byte 0x8e in position 1759",
     "stdout": "static9_windows.stdout.txt", "equals_cloud": False,
     "root_cause": "verify_p0_candidate.py:447 Path(m.__file__).read_text() has no encoding argument",
     "kept_as_first_failure": True},
   "static9_pythonutf8_1": {"rc": 0, "pass": 9, "fail": 0, "stdout": "static9_windows_utf8mode.stdout.txt",
     "equals_cloud": "results/static9.txt after CR normalisation"},
   "selftest37_and_11_pythonutf8_1": {"rc": [0, 0],
     "note": "37/37 and 11/11 are also green in UTF-8 mode; the locale dependency is only in the verifier"},
   "pyserial35_api_stub_probe": {"rc": 0, "SetCommState_before": 5, "SetCommState_after": 0,
     "same_value_setters_probe": 3, "raw_bytes_read_before_after": [64, 64],
     "state_validator_before_after": [False, True],
     "stdout_native_locale": "upstream35_probe_default_locale.stdout.txt",
     "stdout_utf8_mode": "upstream35_probe.stdout.txt",
     "equals_cloud": "results/upstream35_setcommstate_probe.txt after CR normalisation",
     "candidate_used": "local Git index blob export, not the copy shipped in the zip"},
   "independent_encoding_safe_substance_check": {"rc": 0, "script": "check_static_port_substance.py",
     "output": "static_port_substance.stdout.txt",
     "negative_control": "static_port_substance_negative_control.txt (rc=1 over the pre-patch blob)"},
   "no_transport_import": "serial and pyocd absent from sys.modules after importing the collector module"},
 "evidence_archival": {
   "line_endings": "every artefact stored in the repo is LF-only",
   "normalisation_record": "stdout_crlf_normalisation.json (original bytes, CR count, original sha256, normalised sha256 per file)",
   "reason": "Windows Python stdout redirection emits CRLF; core.autocrlf=true would otherwise silently change committed text bytes",
   "manifest": "SHA256SUMS.txt, self-excluded to avoid a circular identity",
   "raw_serial_bytes_archived_this_round": 0},
 "device_actions": [],
 "quotas_consumed": {"COM5_open": 0, "downloads": 0, "target_flash_read": 0, "dap_sessions": 0,
   "resets": 0, "motor_actions": 0, "config_writes": 0, "fault_clears": 0,
   "board_commands_sent": 0, "dmm_readings": 0},
 "prior_quota_note": "the exhausted COM5 allowance of earlier continuation rounds does not carry over; no field action taken this round"
}
out = Path('reports/reviews/phase8-motor-m7-p0-serial-settings-r2/identities.json')
with open(out, 'w', encoding='utf-8', newline='\n') as f:
    json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    f.write('\n')
print('written', out, out.stat().st_size)
