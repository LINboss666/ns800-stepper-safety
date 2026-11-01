#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8 诊断第 0 条(源码审计)的机器可复核版。

只做两件事, 都是零设备、零写盘、零构建:
  A. 逐条断言"我引用的那一行里确实有我引用的那段话"(行号 + 子串), 并按 git ls-files 标注
     该引用件是**远端可复算的仓库件**还是**本机专有件**(厂商包被 .gitignore 排除)。
  B. 逐条断言审计所依赖的**观察件**字节身份(长度/散列/首尾内容), 让判词不是照抄叙述。

任何一条不符就 problems>0 并以退出码 1 结束 —— 本审计不允许"记得是这样"。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("D:/ns800")
BSP = "rt-thread-master/rt-thread-master/bsp/novosns/ns800"
RT = "rt-thread-master/rt-thread-master"
APP = BSP + "/ns800rt7p65-nssinepad/applications"
PKG_SPI_H = (BSP + "/ns800rt7p65-nssinepad/packages/novosns-series-latest/"
             "NS800RT7XXX/StdDriver/Inc/spi.h")
PKG_SHA = "90c9a1da446b3a6d0660d9a5b3eb444dae047135ef6c3942c9d399453cfabbaf"
REV = "reports/reviews/phase8-m8-relative-protection-r3"

# (标签, 仓内相对路径, 1-based 行号, 该行必须包含的子串)
CITES = [
    # --- 启动阶段5 的调用者与其线程优先级 --------------------------------------------
    ("main->supervisor_boot", APP + "/main.c", 51, "boot = supervisor_boot();"),
    ("supervisor->storage", APP + "/supervisor.c", 461, "e = ns_storage_init();"),
    ("stage5-done-line", APP + "/supervisor.c", 463, "[BOOT] 5 storage"),
    ("storage-init", APP + "/ns_storage.c", 138, "rt_err_t ns_storage_init(void)"),
    ("scan-banner", APP + "/ns_storage.c", 147, "[FLASH] scanning 1 MiB event log"),
    ("scan-call", APP + "/ns_storage.c", 148, "e=scan_log();"),
    ("scan-def", APP + "/ns_storage.c", 63, "static rt_err_t scan_log(void)"),
    ("scan-loop-bound", APP + "/ns_storage.c", 70, "for(i=0;i<NS_LOG_SLOTS;++i)"),
    ("scan-per-slot-read", APP + "/ns_storage.c", 72, "ns_flash_read(NS_LOG_BASE+i*256u,page,256)"),
    ("scan-yield-cadence", APP + "/ns_storage.c", 83, "if((i&63u)==63u) rt_thread_mdelay(1);"),
    ("log-size", APP + "/ns_storage.h", 10, "#define NS_LOG_SIZE         0x100000u"),
    ("log-slots", APP + "/ns_storage.h", 15, "#define NS_LOG_SLOTS         (NS_LOG_SIZE / 256u)"),
    ("main-prio", BSP + "/ns800rt7p65-nssinepad/rtconfig.h", 119, "#define RT_MAIN_THREAD_PRIORITY 10"),
    ("finsh-prio", BSP + "/ns800rt7p65-nssinepad/rtconfig.h", 124, "#define FINSH_THREAD_PRIORITY 20"),
    ("prio-max", BSP + "/ns800rt7p65-nssinepad/rtconfig.h", 72, "#define RT_THREAD_PRIORITY_MAX 32"),
    # --- 一次 ns_flash_read 内部: 有界限的那半 ---------------------------------------
    ("flash-read-entry", APP + "/ns_flash.c", 164,
     "rt_err_t ns_flash_read(uint32_t a, void *p, rt_size_t n)"),
    ("flash-take-lock", APP + "/ns_flash.c", 87, "rt_mutex_take(&lock, RT_WAITING_FOREVER)"),
    ("flash-wait-ready", APP + "/ns_flash.c", 171, "e = wait_ready(NSF_ERASE_MS);"),
    ("flash-wait-yields", APP + "/ns_flash.c", 40, "rt_thread_mdelay(1)"),
    ("flash-page-read", APP + "/ns_flash.c", 175, "e = read_raw(a, dst, k);"),
    ("flash-read-raw-xfer", APP + "/ns_flash.c", 58, "rt_spi_send_then_recv(&spi, c, 4, p, n)"),
    ("flash-page-size", APP + "/ns_flash_config.h", 15, "#define NSF_PAGE_SIZE      256u"),
    ("flash-spi-hz", APP + "/ns_flash_config.h", 12, "#define NSF_SPI_HZ         1000000u"),
    ("flash-erase-ms", APP + "/ns_flash_config.h", 17, "#define NSF_ERASE_MS       1500"),
    ("flash-jedec-print", APP + "/ns_flash.c", 141, "[FLASH] CS=%d JEDEC="),
    # --- 无界限的那半: SPI 核心 -> BSP -> 厂商寄存器轮询 -------------------------------
    ("core-send-then-recv", RT + "/components/drivers/spi/dev_spi_core.c", 368,
     "rt_err_t rt_spi_send_then_recv(struct rt_spi_device *device,"),
    ("core-xfer-call-1", RT + "/components/drivers/spi/dev_spi_core.c", 408,
     "result = device->bus->ops->xfer(device, &message);"),
    ("core-xfer-call-2", RT + "/components/drivers/spi/dev_spi_core.c", 423,
     "result = device->bus->ops->xfer(device, &message);"),
    ("core-configure-locks", RT + "/components/drivers/spi/dev_spi_core.c", 258,
     "rt_err_t result = spi_lock(device->bus);"),
    ("bsp-xfer-entry", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 203,
     "static rt_ssize_t ns800_spi_xfer(struct rt_spi_device *device"),
    ("bsp-byte-loop", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 225, "while (length)"),
    ("bsp-byte-timed", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 231,
     "recv_buf[0] = SPI_transmitReceive(instance, send_buf[0]);"),
    ("bsp-recv-only", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 244,
     "recv_buf[0] = SPI_transmitReceive(instance, 0xFF);"),
    ("bsp-length-decrement", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 252, "length--;"),
    ("bsp-disable-module", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 188,
     "SPI_disableModule(spi->config->Instance);"),
    ("bsp-enable-module", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 198,
     "SPI_enableModule(spi->config->Instance);"),
    ("bsp-clock-once", BSP + "/libraries/HAL_Drivers/drivers/drv_spi.c", 329,
     "INIT_BOARD_EXPORT(rt_hw_spi_init);"),
    ("vendor-rx-spin", PKG_SPI_H, 479, "while (spi->SPRFSR.BIT.RFDN == SPI_FIFO_RXEMPTY)"),
    ("vendor-rx-spin-body-empty", PKG_SPI_H, 480, "{"),
    ("vendor-no-return-before-flag", PKG_SPI_H, 483, "return READ_REG(spi->SPDR.WORDVAL);"),
    ("vendor-rxempty-zero", PKG_SPI_H, 393, "SPI_FIFO_RXEMPTY = 0x0000U,"),
    # --- 为什么"零字节/零回显"是判据而不是猜测 ------------------------------------------
    ("finsh-echo-default-on", RT + "/components/finsh/shell.c", 547, "shell->echo_mode = 1;"),
    ("finsh-echo-print", RT + "/components/finsh/shell.c", 937, 'rt_kprintf("%c", ch);'),
    ("finsh-starts-in-components-init", RT + "/components/finsh/shell.c", 1062,
     "INIT_APP_EXPORT(finsh_system_init)"),
    ("components-init-in-main-thread", RT + "/src/components.c", 193, "rt_components_init();"),
    ("safety-prio", APP + "/safety_thread.c", 44, "#define SAFETY_THREAD_PRIO    4"),
    ("safety-timeout", APP + "/safety_thread.c", 45, "#define SAFETY_RECV_TIMEOUT   500"),
    ("safety-event-recv", APP + "/safety_thread.c", 167,
     "e = rt_event_recv(safety_event_handle(), SAFETY_EVT_MASK_ALL,"),
    # --- 阶段5 时刻**只有**这三个线程存在(main / safety / finsh) ------------------------
    ("diag-created-later", APP + "/diagnosis.c", 526, "/* prio 9, 100Hz */"),
    ("flog-worker-after-scan", APP + "/ns_storage.c", 152, 'rt_thread_init(&worker,"flog",log_worker'),
    ("log-prio-macro", APP + "/ns_flash_config.h", 21,
     "#define NS_LOG_PRIORITY    (RT_THREAD_PRIORITY_MAX - 3)"),
    ("stage5-comment-seconds", APP + "/supervisor.c", 460, "刻意排在 Safety Thread 之后"),
]

# (标签, 仓内相对路径(可空), 期望 sha256 前缀或 None, 期望字节数或 None, 判定说明)
def observations():
    s3 = REV + "/offline/fieldcont-d9ff664-20261031T200214Z-41860/S_S3"
    dg = REV + "/offline/diag-com5-20261031T234328Z-48972/sess_diag"
    m5 = "reports/reviews/phase8-motor-m5-fault-log/cp1/final_pass/002_version.bin"
    out = []
    boot = (ROOT / s3 / "01_rst.boot.rx.bin").read_bytes()
    out.append(("boot-capture-len", s3 + "/01_rst.boot.rx.bin", len(boot) == 1027,
                "复位后被动收到 1027 B(不是 0)" if len(boot) == 1027 else "len=%d" % len(boot)))
    out.append(("boot-capture-ends-at-scan", s3 + "/01_rst.boot.rx.bin",
                boot.endswith(b"[FLASH] scanning 1 MiB event log...\r\n"),
                "流的最后一段正是阶段5的那一行" if boot.endswith(b"[FLASH] scanning 1 MiB event log...\r\n")
                else repr(boot[-60:])))
    j = boot.find(b"[FLASH] CS=22 JEDEC=EF 40 17")
    sc = boot.find(b"scanning 1 MiB")
    out.append(("boot-capture-has-jedec-before-scan", s3 + "/01_rst.boot.rx.bin",
                0 <= j < sc,
                "扫描行之前有一次**已完成**的多字节 SPI 读(JEDEC 3 字节)"
                if 0 <= j < sc else "jedec=%d scan=%d" % (j, sc)))
    out.append(("boot-capture-has-no-stage5-done", s3 + "/01_rst.boot.rx.bin",
                b"[BOOT] 5 storage" not in boot,
                "同一段流里没有阶段5 的完成行" if b"[BOOT] 5 storage" not in boot else "有完成行!"))
    out.append(("boot-capture-prompt-before-scan", s3 + "/01_rst.boot.rx.bin",
                0 <= boot.find(b"msh ") < boot.find(b"scanning 1 MiB"),
                "shell 提示符在扫描行**之前**就出现过(所以 shell 线程当时是活的)"
                if 0 <= boot.find(b"msh ") < boot.find(b"scanning 1 MiB") else "提示符位置不符"))
    second = (ROOT / s3 / "02_sysboot.rx.bin").read_bytes()
    out.append(("second-window-zero", s3 + "/02_sysboot.rx.bin", len(second) == 0,
                "复位窗口之后的第二个窗口 0 B" if len(second) == 0 else "len=%d" % len(second)))
    p60 = (ROOT / dg / "01_passive60.rx.bin").read_bytes()
    out.append(("passive60-zero", dg + "/01_passive60.rx.bin",
                len(p60) == 0 and hashlib.sha256(p60).hexdigest().startswith("e3b0c442"),
                "60 s 被动窗 0 B(sha=e3b0c442.. 空散列)"
                if len(p60) == 0 and hashlib.sha256(p60).hexdigest().startswith("e3b0c442")
                else "len=%d" % len(p60)))
    ver = (ROOT / dg / "02_ver.rx.bin").read_bytes()
    out.append(("version-response-zero", dg + "/02_ver.rx.bin", len(ver) == 0,
                "发出 version 后 15 s 步窗内 0 B —— 连回显都没有" if len(ver) == 0
                else "len=%d" % len(ver)))
    old = (ROOT / m5).read_bytes()
    out.append(("historic-echo-proof", m5, old.startswith(b"version\r\n"),
                "同一块板的既存真实件 002_version.bin(154 B) 以回显的 version 开头 "
                "=> 这套固件回显是开的、主机->板 TX 曾真实到达" if old.startswith(b"version\r\n")
                else repr(old[:24])))
    # 既往"Halted"到底是谁的痕迹: 计划参数必须机器可判(它决定 DAP 探针该怎么读)
    plan = json.loads((ROOT / REV / "field/plan.json").read_text(encoding="utf-8"))
    rb = plan["tools"]["readback"]["argv"]
    rs = plan["tools"]["reset"]["argv"]
    out.append(("plan-readback-halts-on-connect", REV + "/field/plan.json",
                "connect_mode=halt" in rb, "readback argv=%s" % (rb,)))
    out.append(("plan-reset-uses-attach", REV + "/field/plan.json",
                ["--connect", "attach"] == rs[rs.index("--connect"):rs.index("--connect") + 2]
                if "--connect" in rs else False,
                "reset argv=%s" % (rs,)))
    return out


def tracked(rel):
    p = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=str(ROOT),
                       capture_output=True)
    return p.returncode == 0


def evaluate(items, lines_cache, quiet=False):
    """逐条断言 (label, rel, line, substring); 返回不符清单。"""
    bad = []
    for label, rel, ln, want in items:
        f = ROOT / rel
        if not f.exists():
            bad.append(("CITE_MISSING", label, rel))
            continue
        if rel not in lines_cache:
            lines_cache[rel] = f.read_text(encoding="utf-8", errors="replace").splitlines()
        txt = lines_cache[rel]
        if ln > len(txt):
            bad.append(("CITE_LINE_PAST_EOF", label, rel, ln, len(txt)))
            continue
        got = txt[ln - 1]
        if want not in got:
            bad.append(("CITE_TEXT_MISMATCH", label, rel, ln, repr(got.strip()[:90])))
            continue
        if not quiet:
            print("OK  %-32s %-10s %s:%d" % (label, "TRACKED" if tracked(rel) else "LOCAL_ONLY",
                                             Path(rel).name, ln))
    return bad


# 元负控: 检查器自己必须**会**失败, 否则它宣布的 0 problems 没有意义。
# 三条都是同一份真文件上的"错一位"版本, 期望各命中一种具名失败。
MUTANTS = [
    ("NEG-line-off-by-one", CITES[0][1], CITES[0][2] + 1, CITES[0][3]),
    ("NEG-wrong-substring", CITES[0][1], CITES[0][2], "SPI_FIFO_RXEMPTY"),
    ("NEG-missing-file", "applications/no_such_file_here.c", 1, "x"),
]


def main():
    lines_cache = {}
    # 厂商头必须先是**本机这份**, 否则行号无意义
    pkg = ROOT / PKG_SPI_H
    pkg_sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
    bad = []
    if pkg_sha != PKG_SHA:
        bad.append(("VENDOR_HEADER_IDENTITY", pkg_sha[:12], PKG_SHA[:12]))
    bad += evaluate(CITES, lines_cache)
    n = len(CITES)
    for b in bad:
        print("BAD %s" % (b,))
    probs = len(bad)
    print("\n--- 观察件(判据的输入, 同样逐条断言) ---")
    for label, rel, ok, note in observations():
        if ok:
            print("OK  %-32s %s" % (label, rel.split("/")[-1]))
        else:
            print("BAD %-32s %s" % (label, note))
            probs += 1
    mbad = evaluate(MUTANTS, {}, quiet=True)
    reasons = sorted({b[0] for b in mbad})
    if len(mbad) != 3:
        print("BAD NEGATIVE_CONTROL fired=%d want=3 reasons=%s" % (len(mbad), reasons))
        probs += 1
    else:
        print("OK  NEGATIVE_CONTROL fired=3/3 reasons=%s" % reasons)
    print("\nCITES=%d  VENDOR_HEADER_SHA_LOCAL=%s..(仓库外件, 远端不可复算)" % (n, pkg_sha[:12]))
    print("AUDIT_PROBLEMS=%d  (0 => 审计的每一行引用与每一条观察都按本机字节成立)" % probs)
    return 0 if probs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
