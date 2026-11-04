#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R7F7 离线独立复算(0 设备动作, 不碰 COM5, 不碰 DAP, 不起 LA)。

把 A 包里的每一条**声称**回到本机可实读的事实上重算: 包散列、镜像四方对账、
AXF 装载段逐字节、冻结源三方一致、validate-plan 与 stub 整包重跑、needle 回产品
工作树逐条命中、A 的 host C 模型在本机重编译重跑并逐字节比对、额度与边界口径、
入库件泄漏扫描。判据工具先证明自己"会命中"(must-FIRE), 才允许它宣布干净。

环境变量: R7F7_SCRATCH(仓外工作目录) / R7F7_GCC(主机 C 编译器) / R7F7_ZIP(整包原件,
只登记散列不入库) / R7F7_APPROVAL_ROOT(核准根目录, 用于证现场账本仍未开)。
缺任一可选环境时对应判据登记 NOT_CHECKED, 不冒充已跑。输出里的本机绝对路径统一
掩成 <REPO>/<SCRATCH> 再入库。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import struct
import tempfile
import time
from pathlib import Path

RD = Path(__file__).resolve().parents[1]           # .../phase8-m8-r7f7-newimage-static
REPO = Path(__file__).resolve().parents[4]         # 仓库根(tools/轮次/reviews/reports/根)
APP_REL = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
           "ns800rt7p65-nssinepad/applications")
FW = "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn"
R7F7_REL = "reports/reviews/phase8-m8-r7f7-newimage-static"
R7F2B_REL = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
EXECUTOR = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"

ACCEPTED = "00718b49e65546aa0bae84006059011d68284c4a"
TIP = "7a08f9d56e69c66b93695cb201a7a3ecefe2b91a"
BIN_SHA = "23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963"
AXF_SHA = "0f421595ac940863c4d38a2791f31bc8811fc3b8fa40062782f2ac7505c317f6"
BM_SHA = "b172a279b75ce96220fded885459642a614408fa8319d660ab69c7113a75dc27"
EXEC_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"
PRODUCT_TREE = "32470c7ed44ef6ef5f762490a5416912a336cdd9"
B502 = "502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a"
BUILD_HEAD = "e1d7f3d90415fdf30344ed95a08065c1373fab01"

# 我按任务书 §2 的表逐行抄成的期望额度(不是从包内 JSON 反推, 两条来源要对上)
QUOTA_EXPECT = {
    "port_open": 1, "download": 1, "readback": 1, "explicit_reset": 1,
    "diag_profile_zero": 1, "diag_profile_learn": 1,
    "tmc_config_apply": 0, "approved_reboot": 0, "config_save": 0,
    "irq_polarity_prep": 0, "diag_profile_commit": 0, "motor_run": 0,
    "motor_run_seconds_total": 0, "motion_cap_seconds_per_run": 0,
    "cleanup_motor_stop": 0, "cleanup_motor_disarm": 0, "cleanup_motor_status": 0,
    "retry": 0, "physical_collision": 0, "blackbox_clear": 0, "dmm_readings": 0,
}
CMD_ORDER = ["ps", "free", "system_status", "safety_status", "motor_status",
             "safety_irq_status", "diag_profile learn_status",
             "diag_profile zero 32", "diag_profile learn_status",
             "diag_profile learn 8 450 550 0", "diag_profile learn_status",
             "system_status", "motor_status"]

fails = []
lines = []


def note(name, ok, detail=""):
    lines.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def info(name, detail):
    lines.append("%s|INFO|%s" % (name, detail))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read(rel):
    return (REPO / rel).read_bytes()


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def git(args, binary=False):
    r = subprocess.run(["git", "-C", str(REPO)] + args,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.returncode, (r.stdout if binary else r.stdout.decode("utf-8", "replace"))


MASKS = []


def mask(text):
    for a, b in MASKS:
        text = text.replace(a, b)
    return text


def put(rel, data):
    p = RD / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    p.write_bytes(data)


def verdict(line):
    """取判定字段: 步骤级行是 NAME|S3|as|PASS|..., 全局行是 NAME|all|PASS|..., 位置不固定。"""
    for f in line.split("|")[1:]:
        if f in ("PASS", "FAIL"):
            return f
    return None


def unesc(pat):
    return re.sub(r"\\(.)", r"\1", pat.strip()).lstrip("^").rstrip("$")


def enum_ordinal(header, typedef, member):
    """按 C 规则算隐式枚举序号(从第一个显式值起逐个 +1)。"""
    m = re.search(r"typedef enum\s*\{([^}]*)\}\s*" + re.escape(typedef), header, re.S)
    if not m:
        return None
    val, cur = None, None
    for line in m.group(1).split(","):
        tok = re.sub(r"/\*.*?\*/", "", line, flags=re.S).strip()
        if not tok:
            continue
        mm = re.match(r"(\w+)(?:\s*=\s*([0-9A-Fa-fx]+))?$", tok)
        if not mm:
            continue
        cur = int(mm.group(2), 0) if mm.group(2) else (cur + 1 if cur is not None else 0)
        if mm.group(1) == member:
            val = cur
    return val


# ------------------------------------------------------------------ 1. 包身份
man = (RD / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
ent = [l.split("  ", 1) for l in man if l.strip() and not l.lstrip().startswith("#")]
files_now = [p for p in RD.rglob("*") if p.is_file()]
bad = []
for h, rel in ent:
    p = RD / rel.strip()
    if not p.exists():
        bad.append("missing " + rel.strip())
    elif sha(p.read_bytes()) != h:
        bad.append("sha " + rel.strip())
note("PKG_ENTRIES_28_PLUS_SELF", len(ent) == 28 and
     not any(e[1].strip() == "SHA256SUMS.txt" for e in ent),
     "清单列 28 件且不含自身(zip 内共 29 件 = 28 + SHA256SUMS.txt; 与 R7F6 同一形状)")
note("PKG_SHA_ALL_OK", not bad, "磁盘==清单, 不符=%d %s" % (len(bad), bad[:5]))
_first = (RD / ent[0][1].strip()).read_bytes()
note("PKG_GATE_FIRES_ON_CORRUPTION", sha(_first) == ent[0][0] and
     sha(_first + b"X") != ent[0][0], "追加 1 字节后同一判据不再放行(会命中)")

zp = os.environ.get("R7F7_ZIP", "")
if zp and os.path.exists(zp):
    zb = open(zp, "rb").read()
    info("PKG_ZIP", "整包 bytes=%d sha256=%s (原件留在仓外不入库)" % (len(zb), sha(zb)))
else:
    info("PKG_ZIP", "NOT_CHECKED(没给 zip 路径; 条目级散列已逐个核过, 整包散列只进回执)")

ident = jload(RD / "IDENTITY.json")
acc = jload(RD / "ACCEPTANCE.json")
plan = jload(RD / "field/plan_r7f7_2369_static.json")
rows = jload(RD / "field/rows_r7f7_2369_static.json")
ndl = jload(RD / "field/needles_newimage.json")
bind = jload(RD / "field/manifest_binding_r7f7_2369.json")
fs = jload(RD / "FROZEN_SOURCE.json")

for tag, blk in (("IDENTITY_PLAN", ident["plan"]), ("IDENTITY_ROWS", ident["rows"]),
                 ("IDENTITY_EXECUTOR", ident["executor"])):
    b = read("reports/reviews/phase8-m8-r7f7-newimage-static/" + blk["path"])
    note(tag + "_BYTES", blk["bytes"] == len(b), "声称=%d 磁盘=%d" % (blk["bytes"], len(b)))
    note(tag + "_SHA", blk["sha256"] == sha(b), blk["sha256"][:16])
bb = read(FW + "/rt-thread-motor-m1-candidate.bin")
note("IDENTITY_IMAGE_BIN", ident["image"]["sha256"] == BIN_SHA and sha(bb) == BIN_SHA and
     ident["image"]["bytes"] == len(bb), "297452 B / 23691e8d 磁盘实读一致")
note("IDENTITY_IMAGE_AXF", ident["image"]["axf_sha256"] == AXF_SHA and
     ident["image"]["axf_bytes"] == os.path.getsize(REPO / FW / "rt-thread-motor-m1.axf") and
     sha(read(FW + "/rt-thread-motor-m1.axf")) == AXF_SHA, "1790048 B / 0f421595")
note("EXECUTOR_IS_REPO_ORIGINAL", sha(read(EXECUTOR)) == EXEC_SHA and
     read(EXECUTOR) == read("reports/reviews/phase8-m8-r7f7-newimage-static/signed_inputs/m8r3_serial_chain.py"),
     "仓内执行器散列未变且与包内见证副本逐字节相同(本轮不改执行器)")
head_now = git(["rev-parse", "HEAD"])[1].strip()
# A 的 development_tip 指的是**包被切出来时的基线**, 不是"必须等于本机 HEAD": 本轮我自己也会提交,
# HEAD 前进是正常的。判据因此核两件事: 声称值 == 本轮起点常量, 且该起点是当前 HEAD 的祖先或本身。
is_anc = subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", TIP, "HEAD"]).returncode == 0
note("IDENTITY_TIP_IS_DEV_BASELINE",
     ident["development_tip"] == TIP and is_anc,
     "A 声明的开发基线 %s 是本轮起点且为当前 HEAD 的祖先/自身(当前 HEAD=%s)" % (TIP[:7], head_now[:7]))
note("ACCEPTANCE_CHECKS_12", len(acc["required_checks"]) == 12 and
     acc["A_real_device_actions"] == 0 and acc["field_result"] == "NOT_RUN",
     "12 项必需判据 / A 设备动作 0 / field_result=NOT_RUN")
note("NOT_VERIFIED_KEPT", len(acc["not_verified"]) == 6 and
     ident["scope_boundaries"]["core_protection_verified"] is False and
     ident["scope_boundaries"]["polarity_measured"] is False,
     "6 项仍未证原样保留, polarity/core 两个 false 未被降级或升级")

# ------------------------------------------------------------------ 2. 镜像四方对账
bm = jload(REPO / FW / "build_manifest.json")
for k, name in (("bin", "BIN"), ("axf", "AXF"), ("map", "MAP"), ("callgraph", "CALLGRAPH")):
    d = read(bm["images"][k]["path"])
    note("IMG_%s_EQ_MANIFEST" % name, bm["images"][k]["sha256"] == sha(d) and
         bm["images"][k]["bytes"] == len(d), "%s B sha=%s" % (len(d), sha(d)[:12]))
note("IMG_BIN_FOUR_PARTY_CONSISTENT",
     bm["images"]["bin"]["sha256"] == BIN_SHA == bind["targets"][0]["images"][
         "rt-thread-motor-m1-candidate.bin"]["sha256"] == ident["image"]["sha256"],
     "A IDENTITY / 派生绑定 / 真实构建 manifest / 磁盘 四方同一")
note("IMG_BINDING_CITES_REAL_MANIFEST", bind["source_build_manifest_sha256"] == BM_SHA and
     sha(read(FW + "/build_manifest.json")) == BM_SHA, "派生绑定引的 b172a279 == 磁盘真实构建清单")
note("IMG_BINDING_IS_NOT_A_NEW_BUILD", bind["kind"].startswith("A_LIGHTWEIGHT_S0_BINDING") and
     bind["accepted_source_commit"] == ACCEPTED, "kind=%s(自陈非新构建)" % bind["kind"])
note("IMG_BUILD_HEAD_CONSISTENT", bm["head_at_build"] == bm["baseline_commit"] == BUILD_HEAD ==
     plan["identity_binding"]["tested_image_build_commit"] == bind["actual_build_time_HEAD"],
     "构建时 HEAD=e1d7f3d 三处登记一致; 签收源码提交 %s 是交付件所在提交" % ACCEPTED[:7])
note("IMG_BUILD_ROUND_DEVICE_0", bm["hardware_actions_this_round"] == 0, "该镜像构建轮设备动作 0")
note("IMG_PROGRAM_SIZE_RECORDED", bm["program_size"].startswith("Program Size:"), bm["program_size"])
note("IMG_B502_HISTORICAL_ONLY", ident["image"]["before_execution"] == "NOT_ON_BOARD" and
     plan["identity_binding"]["board_image_before"]["sha256"] == B502 and
     "Historical" in plan["identity_binding"]["board_image_before"]["kind"] and B502 != BIN_SHA,
     "新版执行前 NOT_ON_BOARD; b502(502dca5d) 只作历史证明, 下载后不再当作当前在板身份")

axf = read(FW + "/rt-thread-motor-m1.axf")
note("AXF_ELF32_LE", (axf[4], axf[5]) == (1, 1), "ELF%d 小端" % (axf[4] * 32))
e_phoff, = struct.unpack_from("<I", axf, 28)
e_phnum, = struct.unpack_from("<H", axf, 44)
segs = []
for i in range(e_phnum):
    o = e_phoff + i * 32
    p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz = struct.unpack_from("<IIIIII", axf, o)
    if p_type == 1:
        segs.append((p_paddr, p_offset, p_filesz, p_memsz))
note("AXF_PTLOAD_ONE", len(segs) == 1, "e_phnum=%d, PT_LOAD 段=%d" % (e_phnum, len(segs)))
if len(segs) == 1:
    paddr, off, filesz, memsz = segs[0]
    seg = axf[off:off + filesz]
    note("AXF_FLASH_PADDR_0x08000000", paddr == 0x08000000, "p_paddr=0x%08x" % paddr)
    note("AXF_FILESZ_EQ_BIN", filesz == len(bb), "filesz=%d BIN=%d" % (filesz, len(bb)))
    note("AXF_SEGMENT_BYTE_EQUAL_BIN", seg == bb, "段 sha=%s == BIN sha=%s" % (sha(seg)[:16], sha(bb)[:16]))
    note("AXF_MEMSZ_ABOVE_FILESZ_IS_RAM", memsz > filesz,
         "memsz=%d(含 ZI/RW 映像) > filesz=%d; 回读判据只覆盖 flash 段" % (memsz, filesz))
    tam = bytearray(seg)
    tam[len(tam) // 2] ^= 0xFF
    note("AXF_GATE_FIRES_ON_CORRUPTION", bytes(tam) != bb and seg == bb,
         "把 flash 段中间 1 字节取反后同一逐字节判据不再放行(会命中)")

# ------------------------------------------------------------------ 3. 源码身份
rc, tr_a = git(["ls-tree", "-r", "-t", ACCEPTED])
rc, tr_t = git(["ls-tree", "-r", "-t", TIP])
pat = re.compile(r"^040000 tree ([0-9a-f]{40})\trt-thread-master$", re.M)
ma, mt = pat.search(tr_a), pat.search(tr_t)
note("SRC_PRODUCT_TREE_EQUAL_ACCEPTED_TO_TIP",
     ma and mt and ma.group(1) == PRODUCT_TREE == mt.group(1),
     "rt-thread-master 树在 %s 与 %s 上都是 %s(与 IDENTITY.product_tree_sha1 同值)"
     % (ACCEPTED[:7], TIP[:7], PRODUCT_TREE[:12]))
note("SRC_PRODUCT_TREE_GATE_FIRES", bool(re.search(r"^040000 tree (%s)\t" % PRODUCT_TREE, tr_t, re.M)) and
     not re.search(r"^040000 tree (%s0)\t" % PRODUCT_TREE, tr_t, re.M), "散列改 1 位后不再命中(会命中)")
bad = []
crlf_wt = []
for it in fs["files"]:
    name = Path(it["path"]).name
    gb = git(["cat-file", "blob", "%s:%s" % (ACCEPTED, APP_REL + "/" + name)], binary=True)[1]
    wt = (REPO / APP_REL / name).read_bytes()
    fz = (RD / "frozen_newimage_source" / it["path"]).read_bytes()
    if sha(gb) != it["sha256"] or len(gb) != it["bytes"]:
        bad.append("00718b4 blob!=声称 " + name)
    if wt != gb:
        if wt.replace(b"\r\n", b"\n") == gb:
            crlf_wt.append(name)          # 只是检出时的行尾转换, 不是内容改动
        else:
            bad.append("工作树!=blob(去掉行尾差也不等) " + name)
    if sha(fz) != it["sha256"] or len(fz) != it["bytes"]:
        bad.append("包内冻结!=声称 " + name)
note("SRC_FROZEN_EQ_BLOB_EQ_WORKTREE_ALL10", not bad,
     "10 件 .c 的『包内冻结 / %s 的 blob / 工作树(按行尾归一)』三方逐字节一致, 不符=%d %s"
     % (ACCEPTED[:7], len(bad), bad[:4]))
note("SRC_WORKTREE_CRLF_ONLY_LINE_ENDING_DISCLOSED",
     len(crlf_wt) == 3 and sorted(crlf_wt) == ["motor.c", "supervisor.c", "tmc2209.c"],
     "工作树里这 3 件是 CRLF(git attr=text + autocrlf 检出转换), blob 与包内冻结都是 LF; "
     "内容等价, 但登记为行尾差异不是 0 差异: %s" % sorted(crlf_wt))
note("SRC_FROZEN_GATE_FIRES", sha((RD / "frozen_newimage_source/applications/motor.c").read_bytes()[::-1])
     != fs["files"][3]["sha256"], "把 motor.c 字节反转后同一判据命中(会命中)")
bad = []
for it in ident["source_bindings"]:
    name = it["path"].split("/")[-1]
    gb = git(["cat-file", "blob", "%s:%s" % (TIP, it["path"])], binary=True)[1]
    ids = [l.split()[2] for l in tr_t.splitlines() if l.endswith("\t" + it["path"])]
    if sha(gb) != it["sha256"] or len(gb) != it["bytes"]:
        bad.append("sha/bytes " + name)
    if ids != [it["git_blob"]]:
        bad.append("blob-id %s got=%s want=%s" % (name, ids, it["git_blob"][:12]))
note("SRC_SOURCE_BINDINGS_5_ON_TIP", not bad,
     "5 件(含 diag_relative.h / project_config.h)在 tip 上 sha+bytes+git blob id 全等, 不符=%d %s"
     % (len(bad), bad[:4]))
rc, nonrep = git(["diff", "--name-only", ACCEPTED, TIP])
outs = sorted(l for l in nonrep.splitlines() if not l.startswith("reports/"))
note("SRC_PRODUCT_UNCHANGED_ACCEPTED_TO_TIP", outs == [".gitattributes"],
     "两提交之间非 reports/ 只有 .gitattributes(行尾规则); 产品与主机源码 0 改动 %s" % outs[:4])

# ------------------------------------------------------------------ 4. 计划判据重跑
EX = str(REPO / EXECUTOR)
PL = str(RD / "field/plan_r7f7_2369_static.json")
scratch = os.environ.get("R7F7_SCRATCH", "") or tempfile.mkdtemp(prefix="r7f7_offline_")
SCR = Path(scratch)
RUNID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
LEDGER = SCR / ("ledger_" + RUNID)
STUBOUT = SCR / ("stub_" + RUNID)
LEDGER.mkdir(parents=True, exist_ok=True)
STUBOUT.mkdir(parents=True, exist_ok=True)
# 掩码顺序有实义: SCR(D:/ns800_r7f1_scratch) 与 REPO(D:/ns800) 有前缀包含关系,
# 先替 REPO 会把仓外工作目录写成 <REPO>_r7f1_scratch, 看起来像"在仓库里"。先长后短。
MASKS += [(str(SCR), "<SCRATCH>"), (str(SCR).replace("\\", "/"), "<SCRATCH>"),
          (str(REPO), "<REPO>"), (str(REPO).replace("\\", "/"), "<REPO>"),
          (RUNID, "<RUNID>")]


def run_py(args):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-X", "utf8"] + args, cwd=str(REPO),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


rc, vp, vpe = run_py([EX, "--validate-plan", "--plan", PL, "--repo-root", "."])
vlines = [l for l in vp.splitlines() if l.strip()]
alines = [l for l in (RD / "proof/validate_plan.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
note("VP_LINES_58", len(vlines) == 58 == len(alines), "本机=%d A 包=%d" % (len(vlines), len(alines)))
kvdiff = [(x, y) for x, y in zip(alines, vlines)
          if x.split("|")[0] != y.split("|")[0] or verdict(x) != verdict(y)]
note("VP_NAME_AND_VERDICT_IDENTICAL_TO_A", not kvdiff, "58 行的判定名与 PASS/FAIL 逐行相同, 差异=%d" % len(kvdiff))
bodydiff = [(x, y) for x, y in zip(alines, vlines) if x != y]
note("VP_ONLY_PLAN_PATH_DIFFERS", len(bodydiff) == 1 and "plan=" in bodydiff[0][0] and
     bodydiff[0][0].startswith("PLAN_VALIDATE_SUMMARY"),
     "58 行里唯一不同是末行 plan= 路径(A 在自己沙箱) -> 判据文本同源")
fl = [l.split("|")[0] for l in vlines if verdict(l) == "FAIL"]
note("VP_SINGLE_RAW_FAIL_IS_MOTION_CAP", fl == ["PLAN_MOTION_CAP_BUDGET"],
     "原始 FAIL=%s; A 按空运动包裁为 N/A 并保留原 FAIL, 未改执行器也未填假运动额度" % fl)
m = re.search(r"PLAN_VALIDATE_SUMMARY\|checks=(\d+)\|fail=(\d+)\|msh_commands=(\d+)", vp)
note("VP_SUMMARY_57_1_74", bool(m) and m.groups() == ("57", "1", "74"),
     "checks/fail/msh=%s" % str(m.groups() if m else "取不到"))
note("VP_REPLAY_MATCHES_ON_A_TEXT_TOO",
     all(verdict(l) in ("PASS", "FAIL") for l in alines if not l.startswith("PLAN_VALIDATE_SUMMARY")) and
     [l.split("|")[0] for l in alines if verdict(l) == "FAIL"] == fl,
     "同一判据在 A 原文上也得到唯一 FAIL=%s" % fl)
put("proof/validate_plan_local.txt", mask(vp))

rc, ni, nie = run_py([EX, "--approval-init", "--approval-id", "A_OFFLINE_STUB_R7F7_RERUN",
                      "--approval-root", str(LEDGER), "--plan", PL, "--repo-root", "."])
note("STUB_INIT_RC0", rc == 0, "rc=%d %s" % (rc, mask(nie).strip()[:100]))


def parse_init(txt):
    for l in txt.splitlines():
        if l.startswith("APPROVAL_INIT|"):
            f = dict(x.split("=", 1) for x in l.split("|")[1:])
            return json.loads(f["caps"]), json.loads(f["identities"])
    return None, None


caps, ids = parse_init(ni)
A_caps, A_ids = parse_init((RD / "proof/stub_init.txt").read_text(encoding="utf-8"))
note("STUB_INIT_CAPS_EQ_A", caps == A_caps, "21 项额度与 A 沙箱完全一致")
note("STUB_INIT_CAPS_EQ_TASKTABLE", caps == QUOTA_EXPECT,
     "额度也==我按任务书 §2 抄的期望表(6 项为 1, 其余 15 项为 0)")
note("STUB_INIT_IDENTITIES_EQ_A", ids == A_ids,
     "executor/plan/image 三散列与 A 一致: %s" % json.dumps(ids or {}, sort_keys=True)[:120])
put("proof/stub_init_local.txt", mask(ni))

rc, sd, sde = run_py([EX, "--dispatch", "--transport", "stub", "--backend", "sim",
                      "--profile", "healthy", "--plan", PL, "--repo-root", ".",
                      "--out", str(STUBOUT), "--approval-id", "A_OFFLINE_STUB_R7F7_RERUN",
                      "--approval-root", str(LEDGER)])
note("STUB_DISPATCH_RC0", rc == 0, "rc=%d %s" % (rc, mask(sde).strip()[:100]))
note("STUB_ALL_FOUR_STAGES_OK", all(("|stage=S%d|status=OK" % i) in sd for i in range(4)),
     "S0 离线绑定 -> S1 下载 -> S2 回读 -> S3 会话 全 OK(夹具, 无设备)")
used = None
for l in sd.splitlines():
    if l.startswith("DISPATCH_SUMMARY"):
        mm = re.search(r"quota_used=(\{.*?\})\|profile", l)
        used = json.loads(mm.group(1)) if mm else None
note("STUB_QUOTA_USED_SIX_POSITIVES", used == {"diag_profile_learn": 1, "diag_profile_zero": 1,
                                               "download": 1, "explicit_reset": 1, "port_open": 1,
                                               "readback": 1}, "quota_used=%s" % used)
note("STUB_NO_ZERO_QUOTA_CONSUMED", bool(used) and not [k for k in QUOTA_EXPECT
                                                        if QUOTA_EXPECT[k] == 0 and k in used],
     "任务书里 0 额度的动作一次也没被消耗")
lf = {}
for l in sd.splitlines():
    if l.startswith("DISPATCH|stage=S0"):
        lf = json.loads(l.rstrip("\n").split("|", 4)[4])["facts"]
af = json.loads((RD / "proof/stub_dispatch.txt").read_text(encoding="utf-8")
                .splitlines()[0].rstrip("\n").split("|", 4)[4])["facts"]
PATHISH = ("plan_source", "plan_snapshot_path", "executor_path", "approval_id")
kv = sorted(k for k in set(lf) | set(af) if str(lf.get(k)) != str(af.get(k)) and k not in PATHISH)
note("STUB_S0_FACTS_EQ_A_EXCEPT_PATHS", not kv, "只差键=%s" % kv)
note("STUB_S0_NEEDLES_41", lf.get("needles_total") == 41 == af.get("needles_total"),
     "S0 自报 needles_total=%s" % lf.get("needles_total"))
note("STUB_S0_IMAGE_PLAN_IDENTITY", lf.get("image_sha256") == BIN_SHA and
     lf.get("plan_sha256") == ident["plan"]["sha256"] and lf.get("image_bytes") == 297452 and
     lf.get("plan_placeholders") == 0 and lf.get("plan_forbidden_hits") == 0 and
     lf.get("executor_sha256") == EXEC_SHA, "S0 现场重算的镜像/计划/执行器身份与声称一致")
sm = re.search(r"DISPATCH\|stage=S3\|status=OK\|kind=\|(\{.*\})", sd)
note("STUB_S3_STEPS_16", bool(sm) and json.loads(sm.group(1))["steps"] == 16 and
     json.loads(sm.group(1))["disarm"] == "NOT_ARMED",
     "S3 steps=%s disarm=%s(15 计划步骤+1 装配) 且未撤能动作"
     % (json.loads(sm.group(1))["steps"] if sm else "?",
        json.loads(sm.group(1)).get("disarm") if sm else "?"))
put("proof/stub_dispatch_local.txt", mask(sd))

rc, nn, nne = run_py([EX, "--prove-needles", str(RD / "field/needles_newimage.json"),
                      "--source-root", str(RD / "frozen_newimage_source")])
tail = [l for l in nn.splitlines() if l.startswith("NEEDLES|")]
note("NEEDLES_FROZEN_41_UNPROVEN_0", rc == 0 and tail and tail[-1] == "NEEDLES|total=41|unproven=0",
     tail[-1] if tail else "无汇总行")
note("NEEDLES_FILE_HAS_41", len(ndl["needles"]) == 41 and
     ndl["accepted_source_commit"] == ACCEPTED, "needles 条数=%d, 声明来源提交=%s"
     % (len(ndl["needles"]), ndl["accepted_source_commit"][:7]))
miss = []
for it in ndl["needles"]:
    src = (REPO / APP_REL / Path(it["source"]).name).read_text(encoding="utf-8", errors="replace")
    if it["literal"] not in src:
        miss.append(it["id"])
note("NEEDLES_ALSO_HIT_LIVE_PRODUCT_SOURCE", not miss,
     "%d 条字面量在产品工作树源码里同样逐条命中(冻结件不是唯一来源), 缺=%s" % (len(ndl["needles"]), miss))
nope = "[DIAG] 这条根本不存在__SELFHECK__"
note("NEEDLES_LIVE_GATE_FIRES", nope not in (REPO / APP_REL / Path(ndl["needles"][0]["source"]).name)
     .read_text(encoding="utf-8", errors="replace"), "不存在的字面量确实判不命中(会命中)")
two = [it["id"] for it in ndl["needles"] if it["id"] in ("learn_ok", "learn_failed")]
note("NEEDLES_OLD_SYNC_LEARN_NEEDLES_ABSENT", not two,
     "旧同步 learn 的两条 OK/FAILED needle 已被异步接口字面量替代(残留=%s)" % two)
put("proof/needles_local.txt", mask(nn))

s3 = [s for s in plan["stages"] if s["id"] == "S3"][0]
steps = s3["steps"]
cmds = [st["cmd"] for st in steps if st.get("cmd") and st["cmd"] != "__watch__"]
watch = [st for st in steps if st.get("cmd") == "__watch__"]
rsts = [st for st in steps if st.get("action") == "reset"]
note("PLAN_STEPS_15_13CMDS_1RESET_1WATCH",
     len(steps) == 15 and len(cmds) == 13 and len(rsts) == 1 and len(watch) == 1,
     "15 步 = 1 复位 + 13 命令 + 1 被动观察(与任务书 §5 的口径一致)")
note("PLAN_COMMAND_ORDER_EQ_TASKTABLE", cmds == CMD_ORDER, "13 条命令顺序: %s" % " > ".join(cmds))
note("PLAN_WATCH_ONE_SECOND_NO_COMMAND", watch[0]["seconds"] == 1 and not watch[0].get("command"),
     "被动窗 1 s 且不带任何命令")
note("PLAN_RESET_ONE_WITH_75S_DRAIN", len(rsts) == 1 and rsts[0]["boot_drain_seconds"] == 75 and
     rsts[0]["quota_name"] == "explicit_reset", "唯一复位步申报并走满 75 s")
note("PLAN_BOOT_FLOOR_DECLARED_AND_ENFORCED",
     plan["observation_bounds"]["boot_window_seconds_min"] == 75 and
     "PLAN_BOOT_WINDOW_COVER" in plan["observation_bounds"]["enforced_by"] and
     any(l.startswith("PLAN_BOOT_WINDOW_COVER|all|PASS") and "75" in l for l in vlines),
     "75 s 是观测下界且被离线覆盖门强制(本机重跑同判 PASS)")
tot = 75 + sum(st.get("seconds", 0) for st in steps)
note("PLAN_SESSION_WITHIN_DEADLINE", tot == 166 and tot <= s3["total_deadline_seconds"] == 210,
     "75 s 启动窗 + 14 个观测/命令窗 %d s = %d s <= 申报总上限 210 s(余 44 s)"
     % (tot - 75, tot))
note("PLAN_QUOTA_EQ_TASKTABLE", plan["quota"] == QUOTA_EXPECT, "计划额度表与任务书 §2 逐项相同(21 项)")
fb = s3["forbidden_commands"]
blob = "\n".join(cmds) + "\n" + "\n".join(" ".join(st["argv"]) for st in steps if st.get("argv"))
hit = [f for f in fb if f in blob]
note("PLAN_NO_FORBIDDEN_SENT", not hit, "17 条禁用命令都没出现在计划命令/argv 里, 命中=%s" % hit)
note("PLAN_FORBIDDEN_GATE_FIRES", [f for f in fb if f in (blob + "\nmotor_start")] == ["motor_start"],
     "注入 motor_start 后同一判据命中(会命中)")
note("PLAN_FORBIDDEN_COVERS_MOTION_AND_PERSIST",
     set(["motor_arm", "motor_start", "motor_disarm", "motor_stop", "tmc_config_apply",
          "safety_polarity_confirm", "safety_irq_enable", "safety_irq_disable",
          "diag_profile commit", "diag_profile tier", "diag_profile clear",
          "diag_profile learn_cancel", "config_save", "blackbox_clear", "fault_reset"]) <= set(fb),
     "起转/apply/极性/IRQ/commit/tier/clear/cancel/保存/清日志/故障复位都在禁用清单里")
ls = [st for st in steps if st["as"] == "learn_start"][0]
note("PLAN_LEARN_MUST_BE_ACCEPTED",
     "REFUSED" in ls["expect_absent"] and "FAILED" in ls["expect_absent"] and
     ls["expect_values"][0]["must"] == {"id": "1", "n": "8", "lo": "450", "hi": "550", "dir": "0"},
     "受理是硬判据: STARTED id=1 n=8 win=450..550 dir=0, 同步骤明禁 REFUSED/FAILED")
jf = [st for st in steps if st["as"] == "job_failed"][0]
note("PLAN_TERMINAL_LOCKED_TO_EXPECTED_FAILURE",
     jf["expect_values"][0]["must"] == {"id": "1", "st": "FAILED", "why": "WINDOW_BROKEN",
                                        "owner": "0", "zero": "1", "learn": "0"} and
     "job DONE" in jf["expect_absent"] and "RUNNING" in jf["expect_absent"] and
     "STARTED" in jf["expect_absent"],
     "终态锁死 id=1 FAILED/WINDOW_BROKEN owner=FREE cred=1/0; DONE/RUNNING/STARTED 都是失败")
zi = [st for st in steps if st["as"] == "zero_static"][0]
note("PLAN_ZERO_32_IS_REAL_ACTION", zi["cmd"] == "diag_profile zero 32" and
     zi["quota"]["name"] == "diag_profile_zero" and zi["quota"]["n"] == 1 and
     "FAILED" in zi["expect_absent"] and "REFUSED" in zi["expect_absent"],
     "zero 32 扣自己那 1 份额度, 且不得以 FAILED/REFUSED 通过")
wl = [(st["as"], w) for st in steps for w in st.get("status_lines_allow", [])]
anch = all(w.startswith("^") and w.endswith("$") for _, w in wl)
req = all(any(unesc(w) == c for c in st.get("expect_contains", []))
          for st in steps for w in st.get("status_lines_allow", []))
note("PLAN_WHITELIST_ANCHORED_AND_REQUIRED", anch and req,
     "%d 条白名单全部整行锚定, 且逐条等于同步骤被正面要求的 expect_contains 文本" % len(wl))
info("PLAN_WHITELIST_LIST", json.dumps(wl, ensure_ascii=False))
note("PLAN_FAILGATE_NOT_GLOBAL_OPEN", len(wl) == 5 and
     [a for a, w in wl if "FAILED" in w] == ["job_failed"],
     "失败词放行只这一处(job_failed), 其余 FAILED/REFUSED 仍首错即停")
note("PLAN_HARDWARE_ACTIONS_0_AND_PORT_COM5", plan["hardware_actions_this_round"] == 0 and
     s3["port"] == "COM5" and s3["reset_after_port_open"] is True and s3["halt_after"] is False and
     s3["first_query_is_boot_probe"] is True, "离线包自陈设备动作 0; COM5 / 开口后复位 / 不 halt")
note("PLAN_POST_RESET_BOOT_IS_FIXTURE_TEXT",
     "Archived b502 boot replay ONLY for stub parsing" in s3["post_reset_boot_provenance"] and
     "Real execution captures raw 75 s" in s3["post_reset_boot_provenance"],
     "S3 的 post_reset_boot 只是 b502 归档文本供 stub 解析, A 明写不是新镜像证据")
pp = plan["operator_power_policy"]
note("PLAN_POWER_POLICY_MATCHES_OPERATOR_PREFERENCE",
     pp["normal_end_powerdown"] == 0 and pp["additional_powercycles"] == 0 and
     pp["initial_power_establish_max"] == 1 and "No action" in pp["if_already_powered"],
     "本轮采纳『已通电就保持供电, 收口不要求断电, 额外断上电 0』(与我转给 A 的口径变更一致)")
note("PLAN_POWER_POLICY_KEEPS_EMERGENCY_PATH",
     "hardware disable takes priority" in pp["unexpected_enable_or_motion"],
     "异常使能/运动时操作者硬件禁能优先, 保持供电口径不挡紧急处置")
note("PLAN_POWER_POLICY_NO_USB_INFERENCE",
     "read actual local operator state" in pp["reported_current_state"],
     "通电状态以现场实报为准, 不由 USB 枚举推断")
note("ROWS_KEYS_COVER_PLAN", all(st["key"] in rows["commands"] for st in steps if st.get("key")),
     "计划里 %d 个 key 都能在 rows 取到(rows 命令共 %d)"
     % (len([st for st in steps if st.get("key")]), len(rows["commands"])))
note("ROWS_DECLARED_HOST_MODEL_NOT_FIELD", "host" in json.dumps(rows["_about"], ensure_ascii=False).lower(),
     "rows 自陈为 host 模型来源: %s" % json.dumps(rows["_about"], ensure_ascii=False)[:150])
note("ROWS_CONTAIN_FORBIDDEN_ONLY_AS_REHEARSAL",
     any("motor_stop" in k for k in rows["commands"]) and not any(f in cmds for f in fb),
     "rows 里有禁用命令的夹具行, 但计划一条也没发(禁用判据看的是计划命令)")

# ------------------------------------------------------------------ 5. 主机 C 复算
gcc = os.environ.get("R7F7_GCC", "")
if gcc:
    MASKS += [(gcc.replace("\\", "/"), "<GCC>"), (gcc, "<GCC>"),
              (os.path.dirname(gcc).replace("\\", "/"), "<GCC_DIR>"),
              (os.path.dirname(gcc), "<GCC_DIR>")]
MASKS += [(sys.executable, "<PY>"), (sys.executable.replace("\\", "/"), "<PY>")]
hp = SCR / "host"
hp.mkdir(parents=True, exist_ok=True)
(hp / "host_probe.c").write_bytes((RD / "proof/host_probe.c").read_bytes())
if gcc and os.path.exists(gcc):
    defm = '-DREVIEW_HOST_MAIN="{}"'.format((REPO / "tests/host/rel_main.c").as_posix())
    cmd = [gcc, "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function",
           "-Wno-unused-parameter", defm,
           "-I", (REPO / "tests/host/shim_rel").as_posix(),
           "-I", (REPO / APP_REL).as_posix(),
           (hp / "host_probe.c").as_posix(), "-o", (hp / "probe.exe").as_posix()]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    blog = r.stderr.decode("utf-8", "replace")
    note("HOST_BUILD_RC0", r.returncode == 0, "rc=%d" % r.returncode)
    info("HOST_BUILD_COMPILER", (subprocess.run([gcc, "--version"], stdout=subprocess.PIPE)
                                 .stdout.decode("utf-8", "replace").splitlines() or ["?"])[0])
    aw = len(re.findall("warning:", (RD / "proof/host_build.stderr.txt")
                        .read_text(encoding="utf-8", errors="replace")))
    note("HOST_BUILD_LINE351_ANCHOR",
         "rel_main.c:351" in (RD / "proof/host_build.stderr.txt").read_text(encoding="utf-8", errors="replace") and
         "if (x) *x = S.mx; if (y) *y = S.my; if (z) *z = S.mz;" in
         (REPO / "tests/host/rel_main.c").read_text(encoding="utf-8", errors="replace"),
         "A 日志指向 rel_main.c:351; 本机同文件该行原文一致 -> 夹具同源。warning 本机=%d A=%d "
         "(差异是编译器版本: -Wmisleading-indentation 需 GCC>=6, 本机 4.9.2 不发)"
         % (len(re.findall("warning:", blog)), aw))
    p = subprocess.run([(hp / "probe.exe").as_posix()], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    norm = p.stdout.replace(b"\r\n", b"\n")
    note("HOST_RUN_RC0_PROBE_ASSERTS", p.returncode == 0,
         "rc=0 表示探针自己的判据成立: id=1 & state=REL_JOB_FAILED & owner=REL_CAP_FREE & zero & !learn & !active")
    note("HOST_OUTPUT_BYTE_EQUAL_TO_A_AFTER_CRLF_NORMALIZE",
         sha(norm) == sha((RD / "proof/host_probe.stdout.txt").read_bytes()),
         "本机 LF 归一后 sha=%s == A 包 stdout sha=%s" % (sha(norm)[:16],
                                              sha((RD / "proof/host_probe.stdout.txt").read_bytes())[:16]))
    note("HOST_CRLF_DIFF_IS_CARRIAGE_ONLY",
         len(p.stdout) - len(norm) == norm.count(b"\n") and p.stdout.count(b"\r") == norm.count(b"\n"),
         "字节差 %d == 行数 => 只是行尾差, 不是内容差" % (len(p.stdout) - len(norm)))
    txt = norm.decode("utf-8", "replace")
    order = re.findall(r"FRAME_BEGIN\|(\w+)", txt)
    note("HOST_FRAME_ORDER", order == ["job_initial", "zero", "job_zero", "learn_start", "job_failed"],
         "帧顺序=%s" % order)
    hs = [l for l in txt.splitlines() if l.startswith("HOST_STATE|")]
    note("HOST_STATE_VALUES", hs == ["HOST_STATE|id=1 state=4 owner=0 zero=1 learn=0 active=0"],
         hs[0] if hs else "无 HOST_STATE 行")
    hdr = (REPO / APP_REL / "diag_relative.h").read_text(encoding="utf-8", errors="replace")
    failed_v = enum_ordinal(hdr, "rel_job_state_t", "REL_JOB_FAILED")
    note("HOST_STATE_4_IS_REL_JOB_FAILED_ENUM", failed_v == 4,
         "按 C 隐式枚举从 rel_job_state_t 头部推算 REL_JOB_FAILED=%s(与 host 行的 state=4 同一身份)"
         % failed_v)
    free_v = enum_ordinal(hdr, "rel_cap_owner_t", "REL_CAP_FREE")
    note("HOST_OWNER_0_IS_REL_CAP_FREE", free_v == 0,
         "REL_CAP_FREE=%s(枚举序号回产品头文件推算, 不是照抄 host 行的 0)" % free_v)
    note("HOST_GATE_FIRES_ON_TAMPERED_OUTPUT",
         sha(norm.replace(b"WINDOW_BROKEN", b"OTHER_REASON")) !=
         sha((RD / "proof/host_probe.stdout.txt").read_bytes()),
         "把 why 改成别的身份后逐字节判据命中(会命中)")
    put("proof/host_probe_local_lf.txt", norm)
    put("proof/host_build_local.stderr.txt", mask(blog))
    put("proof/host_probe_cmd_local.txt", mask(" ".join(cmd)))
else:
    info("HOST_C_RECOMPUTE", "NOT_CHECKED(未给 R7F7_GCC 或路径不存在; 不冒充已跑)")

# ------------------------------------------------------------------ 6. 边界、隐私、未越界
sb = ident["scope_boundaries"]
note("BOUNDARIES_MATCH_TASKBOOK",
     sb["LA_start"] == 0 and sb["probe_rewiring"] == 0 and sb["learn_cancel"] == 0 and
     sb["commit"] == 0 and sb["save"] == 0 and sb["zero"] == 1 and
     sb["learn_request_attempts"] == 1 and sb["accepted_learn_jobs"] == 1 and
     sb["expected_job_terminal"] == "FAILED/WINDOW_BROKEN" and
     sb["expected_collector_owner_after_terminal"] == "FREE" and
     sb["zero_credential_after"] == 1 and sb["learn_credential_after"] == 0 and
     sb["learn_successful_DONE_baseline"] is False and sb["job_active_cancel_verified"] is False,
     "额度/期望终态/未证项与任务书 §2 §5 §6 一致")
note("BOUNDARIES_IDENTIFIER_ISSUE_NOT_MARKED_FIXED",
     "deferred" in sb["archive_identifier_issue"].lower() and
     "not falsely marked fixed" in sb["archive_identifier_issue"].lower(),
     sb["archive_identifier_issue"][:110])
note("IDENTITY_OFFLINE_PROOF_FIELDS", ident["offline_proof"]["real_device_actions_by_A"] == 0 and
     ident["offline_proof"]["stub_stages_executed"] == 4 and ident["offline_proof"]["stub_command_count"] == 13 and
     ident["offline_proof"]["stub_positive_quota_used"] == used,
     "A 自报的离线证据字段(0 设备 / 4 阶段 / 13 命令 / 6 项正额度)与本机重跑一致")
note("IDENTITY_STATUS_AND_FIELD_RESULT", ident["field_result"] == "NOT_RUN" and
     ident["status"].startswith("A_FIXED_SCOPE"), "本轮 A 侧 field_result=NOT_RUN")

# 泄漏口子的三类: 用户主目录片段 / AppData 片段 / 48 位十六进制(私有 DAP UID 的形状),
# 外加"当前登录用户名"这个明文字符串(从环境变量取, 不写进源码, 否则检查器自己就是泄漏)。
# 通用盘符模式 X:/ 试过并**放弃**: A 的包件里合法含有 C:/Keil_v5/...(任务书自带的工具链路径),
# 那不是个人标识; 一刀切会逼我改包件原文。这里登记为口径取舍, 不是把命中删掉。
SL = "[" + chr(92) + chr(92) + "/]"
LEAKS = [(SL + "Users" + SL, "本机用户主目录路径片段"),
         (SL + "AppData" + SL, "AppData 路径片段"),
         (r"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])", "48 位十六进制(疑似私有 DAP UID)")]
USER = os.environ.get("USERNAME", "") or os.environ.get("USER", "")
info("LEAK_SCOPE_DECISION", "三类路径/UID 模式 + 用户名明文(来自环境变量=%s); 通用盘符模式按上述理由放弃"
     % ("<set>" if USER else "<empty>"))
pending = "\n".join(lines) + "\nR7F7_OFFLINE_SUMMARY|<pending>\n"
scan_items = []
for p in sorted(RD.rglob("*")):
    if not p.is_file() or p.suffix in (".png", ".bin", ".axf", ".map", ".htm", ".exe", ".o"):
        continue
    scan_items.append((p.relative_to(REPO).as_posix(), p.read_bytes().decode("utf-8", "replace")))
scan_items.append(("proof/offline_preflight_r7f7.txt(本轮即将写入的日志)", pending))
hits = []
for name, data in scan_items:
    for pat, why in LEAKS:
        if re.search(pat, data):
            hits.append("%s: %s" % (why, name))
    if USER and USER.lower() in data.lower():
        hits.append("当前登录用户名: %s" % name)
note("LEAK_SCAN入库件命中为_0", len(scan_items) >= 30 and not hits,
     "扫 %d 件(含本轮即将入库的日志原文), 命中=%d %s" % (len(scan_items), len(hits), hits[:6]))
# 合成样本用拼接构造: 直接写进源码会被本扫描自己抓到(上一轮就自伤过一次), 那样就分不清
# 是"证据里有"还是"检查器里有"。
probe = ("C" + ":" + chr(92) + "Users" + chr(92) + "x " + "D" + ":" + chr(92) + "AppData" +
         chr(92) + "y " + "a" * 48 + (" " + USER + " " if USER else ""))
nfire = sum(1 for pat, _ in LEAKS if re.search(pat, probe))
note("LEAK_SCAN_GATE_FIRES", nfire == len(LEAKS),
     "对合成样本 %d/%d 条模式命中(会命中才允许宣布干净)" % (nfire, len(LEAKS)))

ALLOWED_TOUCH = (R7F7_REL, "reports/agent_handoffs/phase8-m8-r7f7-newimage-static",
                 R7F2B_REL + "/run_record.md", R7F2B_REL + "/SHA256SUMS.txt", ".gitattributes")
rc, stt = git(["status", "--porcelain"])
touched = [l[3:].strip() for l in stt.splitlines() if l.strip()]
note("ROUND_TOUCHES_NO_PRODUCT_OR_HOST_CODE",
     not [t for t in touched if t.startswith(("rt-thread-master/", "tests/", "packages/"))],
     "本轮工作树改动 %d 项, 全部在 reports/ 与 .gitattributes 里" % len(touched))
out_of_scope = [t for t in touched if not any(t.startswith(p) for p in ALLOWED_TOUCH)]
note("OLD_EVIDENCE_UNTOUCHED", not out_of_scope,
     "旧证据/旧账 0 改动(只允许 run_record.md 追加与本目录), 越界=%s" % out_of_scope[:6])
# 基线用**本轮开始时的 tip**(常量 TIP=7a08f9d), 不是 HEAD: 要证的是"旧 §1-§19 没被改",
# 而本轮自己新写的 §20 允许在本轮内迭代(例如把自伤条数从 16 更正到 17), 那不该把判据钉红。
old_rr = git(["show", "%s:%s" % (TIP, R7F2B_REL + "/run_record.md")], binary=True)[1]
new_rr = (REPO / R7F2B_REL / "run_record.md").read_bytes()
note("RUN_RECORD_IS_APPEND_ONLY", old_rr and new_rr.startswith(old_rr) and len(new_rr) > len(old_rr),
     "%s(本轮起点)版本的 run_record.md 是当前磁盘版的**严格前缀** => 旧 §1-§19 一个字节没被改" % TIP[:7])
miss_sec = [s for s in ("## 16", "## 17", "## 18", "## 19")
            if old_rr.count(("\n" + s).encode()) != 1 or new_rr.count(("\n" + s).encode()) != 1]
note("OLD_SECTIONS_STILL_ONE_EACH", not miss_sec and new_rr.count(b"\n## 20") == 1,
     "§16/§17/§18/§19 各仍出现一次, §20 新增一次; 异常=%s" % miss_sec)
ar = os.environ.get("R7F7_APPROVAL_ROOT", "")
roots = [r for r in re.split(r"[;,]", ar) if r.strip()]
hit_field = []
for r in roots:
    rp = Path(r)
    if rp.is_dir():
        hit_field += [str(x) for x in rp.glob("*M8R7F7-2369-async-job-static*")]
if roots:
    note("FIELD_APPROVAL_LEDGER_ABSENT", not hit_field,
         "扫 %d 个核准根目录, 现场批准身份 M8R7F7-2369-... 无账本(本轮未开): %s" % (len(roots), hit_field))
else:
    info("FIELD_APPROVAL_LEDGER_ABSENT", "NOT_CHECKED(未给 R7F7_APPROVAL_ROOT; 本轮确未开现场身份)")
uid = Path(os.environ.get("R7F7_UID_FILE", "")) if os.environ.get("R7F7_UID_FILE") else None
if uid and uid.exists():
    note("PRIVATE_DAP_UID_FILE_PRESENT_AND_NOT_COMMITTED",
         len(uid.read_bytes().strip()) == 48 and not [
             p for p in RD.rglob("*") if p.is_file() and uid.read_bytes().strip()
             in p.read_bytes()],
         "私有 UID 文件在位(长度 48, 值不打印不入库); 本轮入库件里搜不到该明文 => 现场轮的前置件可用")
else:
    info("PRIVATE_DAP_UID_FILE_PRESENT_AND_NOT_COMMITTED",
        "NOT_CHECKED(未给 R7F7_UID_FILE; 不猜路径)")
note("NO_REAL_TRANSPORT_NO_DEVICE_TOOLS", "--transport real" not in " ".join(sys.argv) and
     all("pyocd" not in a and "UV4" not in a for a in sys.argv),
     "本工具只用 python+git+gcc 与 stub/sim; 未起 real 传输, 未调 pyOCD/Keil/分析仪")

tot_pred = len([l for l in lines if "|PASS|" in l or "|FAIL|" in l])
summary = "R7F7_OFFLINE_SUMMARY|判据=%d|FAIL=%d|%s" % (
    tot_pred, len(fails), ",".join(fails) if fails else "none")
full = "\n".join(lines + [summary]) + "\n"
put("proof/offline_preflight_r7f7.txt", full)
print(full, end="")
sys.exit(1 if fails else 0)
