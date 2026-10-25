/*
 * sp_measure_a0_finsh.h - A0 链专用：主机侧的 FSymTab 注册 + 按名查表分发
 * (任务 phase8-target-measurement-a0-r1，复审项 A0-01)
 *
 * 为什么要有这个文件：原主机夹具把目标侧的命令注册语句整条排除在编译之外
 * (`#if !defined(NS800_SPM_HOST_BACKEND)`)，只能直接调用 spm_cmd()，所以 18 例全绿
 * 也覆盖不到"目标上真正可用的命令名到底是什么"这条接缝。本文件让**同一条生产源语句**
 *     MSH_CMD_EXPORT_ALIAS(spm_cmd, spm, NS800 target measurement A0 control);
 * 在主机变体里也参与编译，注册结果落进一张可按名字查表的结构体，分发严格按
 * rt-thread-master/components/finsh/msh.c:232-249 的 msh_get_cmd() 语义
 *     (rt_strncmp(index->name, cmd, size) == 0 && index->name[size] == '\0')
 * 命令行切分按 msh.c:152-230 的 msh_split() 语义(空白分隔并就地写 '\0'，
 * 不产生空 argv 项；超过 FINSH_ARG_MAX 停止追加)。
 *
 * 与真实 RT-Thread 注册的**已知差异只有两处**，并且都由目标侧 ELF/FSymTab 取证兜底：
 *   1) 不靠链接器段 "FSymTab" 收集，改用静态表 + 构造函数登记(PE/COFF 主机没有该段)；
 *   2) 段属性 rt_section(".rodata.name") 不加。
 * 结构体字段顺序与真实 rtconfig.h(同时开 FINSH_USING_SYMTAB + FINSH_USING_DESCRIPTION
 * + FINSH_USING_OPTION_COMPLETION)下的 struct finsh_syscall 一致：{name, desc, opt, func}。
 * 真实镜像里的段位置、名字串与逐字段身份不在这里证明，在 Keil 构建后的
 * ELF FSymTab 取证里证明(measure-a0.axf)。
 *
 * 用法(只在 NS800_SPM_HOST_BACKEND 变体里)：
 *   - REC 用它替代 <finsh.h> 来展开注册宏；
 *   - 夹具用 spm_msh_exec() 以"整行命令"驱动，不直接调用 spm_cmd()。
 */
#ifndef SP_MEASURE_A0_FINSH_HOST_H
#define SP_MEASURE_A0_FINSH_HOST_H

#include <rtthread.h>
#include <string.h>

typedef long (*syscall_func)(void);           /* 与 finsh.h:32 同一 typedef */

/* 与 components/finsh/finsh.h:170-181 的 struct finsh_syscall 同序 */
struct spm_finsh_syscall
{
    const char         *name;
    const char         *desc;
    const void         *opt;                  /* 真实类型 struct msh_cmd_opt*，本链恒 0 */
    syscall_func        func;
};

#define SPM_FSYM_MAX      8
#define SPM_FINSH_ARG_MAX 10                  /* rtconfig.h:132 */

#ifdef SPM_FINSH_HOST_IMPL
const struct spm_finsh_syscall *spm_fsym_tab[SPM_FSYM_MAX];
int spm_fsym_n;
#else
extern const struct spm_finsh_syscall *spm_fsym_tab[SPM_FSYM_MAX];
extern int spm_fsym_n;
#endif

/*
 * 展开形状对齐 finsh.h:86-92 的 MSH_FUNCTION_EXPORT_CMD(name, cmd, desc, opt)：
 * 命令名来自 **cmd** 参数(#cmd 成字符串)，函数来自 **name** 参数。
 * 因此 MSH_CMD_EXPORT_ALIAS(spm_cmd, spm, desc) 得到的条目是 {"spm", ..., &spm_cmd}，
 * 而 MSH_CMD_EXPORT(spm_cmd, desc) 得到的条目是 {"spm_cmd", ..., &spm_cmd} —— 这正是
 * 复审 A0-01 里"注册出来的是 spm_cmd 不是 spm"的根因。
 */
/* tests/host/shim_ps/rtthread.h 里预置的是**空**注册宏(历史 shim)。本链要把注册接缝变成
 * 真编译的，所以在自己的 TU 里显式接管这两个宏；不改那个既有 shim 文件本身。 */
#ifdef MSH_CMD_EXPORT
#undef MSH_CMD_EXPORT
#endif
#ifdef MSH_CMD_EXPORT_ALIAS
#undef MSH_CMD_EXPORT_ALIAS
#endif

#define MSH_CMD_EXPORT_ALIAS(command, alias, desc)                                     \
    static const char __fsym_##alias##_name[] = #alias;                                \
    static const char __fsym_##alias##_desc[] = #desc;                                 \
    static const struct spm_finsh_syscall __fsym_##alias = {                           \
        __fsym_##alias##_name, FINSH_HOST_DESC(alias, desc) 0, (syscall_func)&command  \
    };                                                                                 \
    static void __attribute__((constructor)) __fsym_##alias##_host_reg(void)            \
    {                                                                                  \
        if (spm_fsym_n < SPM_FSYM_MAX) spm_fsym_tab[spm_fsym_n++] = &__fsym_##alias;    \
    }

#define MSH_CMD_EXPORT(command, desc) MSH_CMD_EXPORT_ALIAS(command, command, desc)

#define FINSH_HOST_DESC(cmd, desc) __fsym_##cmd##_desc,

/* msh.c:232-249 msh_get_cmd() 的等价：按**完整名字**查表，长度外必须正好是 NUL。 */
static syscall_func spm_msh_get_cmd(const char *cmd, int size)
{
    int i;

    for (i = 0; i < spm_fsym_n; i++)
    {
        const struct spm_finsh_syscall *e = spm_fsym_tab[i];

        if (e == RT_NULL) continue;
        if (strncmp(e->name, cmd, size) == 0 && e->name[size] == '\0') return e->func;
    }
    return RT_NULL;
}

/* msh.c:152-230 msh_split() 的等价：就地切分，不产生空 argv 项。 */
static int spm_msh_split(char *line, rt_size_t length, char *argv[])
{
    char     *ptr = line;
    rt_size_t position = 0;
    int       argc = 0;

    while (position < length && argc < SPM_FINSH_ARG_MAX)
    {
        while ((*ptr == ' ' || *ptr == '\t') && position < length)
        {
            *ptr = '\0';
            ptr++;
            position++;
        }
        if (position >= length) break;
        argv[argc++] = ptr;
        while ((*ptr != ' ' && *ptr != '\t') && position < length)
        {
            ptr++;
            position++;
        }
        if (position >= length) break;
        *ptr = '\0';
        ptr++;
        position++;
    }
    return argc;
}

/*
 * msh.c:325-352 _msh_exec_cmd() 的等价：先按第一段的名字查表，查不到就**不**调用任何东西；
 * 查到才切 argv 并经函数指针调用。返回 0 = 命令存在并已执行(*retp 是命令自己的返回码)，
 * 返回 -1 = 名字不在表里(等价 -RT_ERROR)，此时**没有**任何命令函数被调用过。
 */
static int spm_msh_exec(char *line, int *retp)
{
    rt_size_t  cmd0_size = 0;
    rt_size_t  length = strlen(line);
    syscall_func f;
    char      *argv[SPM_FINSH_ARG_MAX];
    int        argc;

    if (line == RT_NULL || retp == RT_NULL) return -1;
    *retp = 0x5A5A;                                  /* 未写入的哨兵：调用方可判定是否真分发 */
    while (cmd0_size < length && line[cmd0_size] != ' ' && line[cmd0_size] != '\t')
        cmd0_size++;
    if (cmd0_size == 0) return -1;

    f = spm_msh_get_cmd(line, (int)cmd0_size);
    if (f == RT_NULL) return -1;

    memset(argv, 0x00, sizeof(argv));
    argc = spm_msh_split(line, length, argv);
    if (argc == 0) return -1;
    *retp = (int)((int (*)(int, char **))f)(argc, argv);
    return 0;
}

#endif /* SP_MEASURE_A0_FINSH_HOST_H */
