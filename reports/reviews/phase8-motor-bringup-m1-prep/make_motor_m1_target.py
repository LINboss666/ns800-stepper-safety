import hashlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED = "ec9eed0e9d9998c38b5dd935ddaea7e74829c2a770272dfe0d675cc6fce4cc09"
ANCHOR = "ea519f29af24c6f9353de67b7bb4d9f60baf5e1a"
TARGET = "rt-thread-motor-m1-candidate"
REL = "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/project.uvprojx"

def replace_tag(block, tag, old, new):
    before = ("<" + tag + ">" + old + "</" + tag + ">").encode("ascii")
    after = ("<" + tag + ">" + new + "</" + tag + ">").encode("ascii")
    if block.count(before) != 1:
        raise SystemExit("STOP: non-unique source field " + tag)
    return block.replace(before, after, 1)

def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: python make_motor_m1_target.py REPO_ROOT OUTPUT_FILE")
    src = Path(sys.argv[1]) / REL
    dst = Path(sys.argv[2])
    raw = subprocess.check_output(["git", "show", ANCHOR + ":" + REL], cwd=Path(sys.argv[1]))
    if hashlib.sha256(raw).hexdigest() != EXPECTED:
        raise SystemExit("STOP: project input hash differs; no output written")
    matches = list(re.finditer(rb"    <Target>\r?\n.*?    </Target>", raw, re.S))
    if len(matches) != 2:
        raise SystemExit("STOP: expected exactly two source targets")
    original = matches[0].group()
    old_root = ET.fromstring(raw)
    old_targets = old_root.findall("./Targets/Target")
    assert [x.findtext("TargetName") for x in old_targets] == ["rt-thread", "rt-thread-measure-a0"]
    block = replace_tag(original, "TargetName", "rt-thread", TARGET)
    block = replace_tag(block, "OutputDirectory", ".\\build\\", ".\\build\\motor_m1\\")
    block = replace_tag(block, "OutputName", "rt-thread", "rt-thread-motor-m1")
    block = replace_tag(block, "ListingPath", ".\\build\\", ".\\build\\motor_m1\\")
    define_path = "./TargetOption/TargetArmAds/Cads/VariousControls/Define"
    defines = old_targets[0].findtext(define_path)
    flags = (
        ", NS800_TARGET_MEASUREMENT_A0=0"
        ", MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=1"
        ", TMC_DRIVE_CONFIG_HW_VALIDATED=1"
        ", STEP_DIAG_ISOLATED_ENV_CONFIRMED=0"
        ", SAFETY_LIMIT_INPUTS_ENABLED=0"
    )
    for flag in flags.split(", ")[1:]:
        assert flag.split("=")[0] not in defines
    block = replace_tag(block, "Define", defines, defines + flags)
    block = replace_tag(block, "UserProg1Name", "fromelf --bin !L --output rtthread.bin",
                        'fromelf --bin !L --output .\\build\\motor_m1\\rt-thread-motor-m1.bin')
    marker = b"  </Targets>"
    assert raw.count(marker) == 1
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    out = raw.replace(marker, block + newline + marker, 1)
    new_root = ET.fromstring(out)
    targets = new_root.findall("./Targets/Target")
    assert len(targets) == 3 and targets[2].findtext("TargetName") == TARGET
    new_matches = list(re.finditer(rb"    <Target>\r?\n.*?    </Target>", out, re.S))
    assert len(new_matches) == 3
    assert [m.group() for m in new_matches[:2]] == [m.group() for m in matches]
    assert ET.tostring(old_targets[0].find("Groups")) == ET.tostring(targets[2].find("Groups"))
    assert len(targets[2].findall("./Groups/Group/Files/File")) == 135
    assert src.resolve() != dst.resolve(), "output must be a new candidate, not the source project"
    with dst.open("xb") as f:
        f.write(out)
    print("CANDIDATE_ONLY", len(out), hashlib.sha256(out).hexdigest())
    print("original_targets_unchanged=2 source_items=135 board_actions=0")

if __name__ == "__main__":
    main()
