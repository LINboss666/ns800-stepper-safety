# -*- coding: utf-8 -*-
"""F22 现场整包唯一一次 dispatch。

为什么要这个 runner: 真实传输的预检查(m8r3_serial_chain_r7f8.py:5530)只认 --approval-token,
不认 --approval-token-file => 我 F21 建的"文件取字节"路线在真实分支上根本走不到(这是覆盖洞,
已登记)。这里不改执行器字节(改了就不是操作者批准的那件 executor 4363af36…), 而是在 **Python 内**
把原话文件的规范字节读出来, 同时喂给 --approval-token / --expected-token / --approval-token-file /
--token-verbatim-file 四个口 => 令牌不经控制台、不经 shell 变量; 若任何一处字节不同,
执行器自己的 TOKEN_SOURCE_CONFLICT / TOKEN_DIGEST_MISMATCH 会在开口前拒。
"""
import pathlib
import subprocess
import sys
import hashlib

REPO = pathlib.Path(r"D:\ns800")
ROUND = REPO / "reports/reviews/phase8-m8-r7f8-executor-adaptation"
EXE = ROUND / "tools/m8r3_serial_chain_r7f8.py"
PLAN = ROUND / "field/plan_r7f8f_2369_real_dispatch_f22.json"
VERBATIM = ROUND / "proof/r7f8f_field_f22_operator_verbatim.txt"
OUT = pathlib.Path(r"D:/ns800_r7f8f_offline/f22/run")
LEDGER = pathlib.Path(r"D:/ns800_r7f8f_offline/approvals")
APPROVAL_ID = "M8-R7F8F-F22-20261106"

raw = VERBATIM.read_bytes()
canon = raw.rstrip(b"\r\n")
text = canon.decode("utf-8")          # 严格 UTF-8: 解不开就直接抛, 不猜内容
print("TOKEN_BYTES|raw=%d sha=%s|canon=%d sha=%s" %
      (len(raw), hashlib.sha256(raw).hexdigest()[:12],
       len(canon), hashlib.sha256(canon).hexdigest()[:12]))

cmd = [sys.executable, str(EXE),
       "--dispatch", "--transport", "real", "--backend", "real",
       "--profile", "response_driven",
       "--plan", str(PLAN),
       "--approval-id", APPROVAL_ID,
       "--approval-root", str(LEDGER),
       "--approval-token", text,
       "--expected-token", text,
       "--approval-token-file", str(VERBATIM),
       "--token-verbatim-file", str(VERBATIM),
       "--port", "COM5",
       "--out", str(OUT),
       "--repo-root", str(REPO)]
r = subprocess.run(cmd, capture_output=True, cwd=str(REPO))
(OUT.parent / "dispatch_out.txt").write_bytes(r.stdout)
(OUT.parent / "dispatch_err.txt").write_bytes(r.stderr)
print("RC=%d" % r.returncode)
sys.stdout.write(r.stdout.decode("utf-8", "replace")[:1500])
sys.stdout.write("\n--STDERR--\n" + r.stderr.decode("utf-8", "replace")[:1200])
