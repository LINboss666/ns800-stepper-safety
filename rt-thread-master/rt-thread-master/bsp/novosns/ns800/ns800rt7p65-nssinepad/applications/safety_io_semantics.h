/*
 * safety_io_semantics.h - 安全输入的数据语义 (Phase 8, 2026-10-22)
 *
 * 这一层只定义"数字电平 / 活动集合 / 有效集合 / 记录语义版本"这些**数据含义**,
 * 不改变任何安全判据、门禁或保护逻辑, 也不是存储层版本:
 *   - LOG1 外层页面版本仍是 1(ns_storage.c), 页长/偏移/CRC 均未变。
 *   - 本文件的 schema 是**应用安全输入载荷版本**, 新记录写 1。
 *
 * 事实前提(见 reports/plans/phase8-safety-chain-acceptance-draft.md 的 B 表):
 * MCU 没有独立的急停输入 GPIO; PC.6/IO70 读的是 H6-5/TMC_ENN 的被动反馈。
 * 因此任何电平位都不得被解释成"急停按钮按下", 也不能反推切断原因。
 */
#ifndef SAFETY_IO_SEMANTICS_H
#define SAFETY_IO_SEMANTICS_H

#include <rtthread.h>
#include "project_board.h"      /* SAFETY_LIMIT_INPUTS_ENABLED + 引脚名 */

/* ===== 安全输入位序(传感器帧掩码与黑匣子 flags 共用, 不得散落魔数) ===== */
#define SAI_BIT_ENN             0u
#define SAI_BIT_LIMIT_MIN       1u
#define SAI_BIT_LIMIT_MAX       2u
#define SAI_BIT_DIAG            3u

#define SAI_MASK_ENN            (1u << SAI_BIT_ENN)
#define SAI_MASK_LIMIT_MIN      (1u << SAI_BIT_LIMIT_MIN)
#define SAI_MASK_LIMIT_MAX      (1u << SAI_BIT_LIMIT_MAX)
#define SAI_MASK_DIAG           (1u << SAI_BIT_DIAG)
#define SAI_MASK_ALL            (SAI_MASK_ENN | SAI_MASK_LIMIT_MIN | \
                                 SAI_MASK_LIMIT_MAX | SAI_MASK_DIAG)

/* 活动集合只由配置决定, **不随解析失败或采样失败缩小**。
 * 目标配置(限位关闭)= ENN + DIAG = 0x9; 限位开启 = 四路 = 0xF。 */
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
#define SAI_ACTIVE_MASK         SAI_MASK_ALL
#else
#define SAI_ACTIVE_MASK         (SAI_MASK_ENN | SAI_MASK_DIAG)
#endif

/* ===== 黑匣子 record.flags 的应用载荷布局(32 位) =====
 * 7:0   fault_code      (保持)
 * 11:8  四路数字电平     bit8=ENN 反馈, 9=LIMIT_MIN, 10=LIMIT_MAX, 11=DIAG (位置保持)
 * 15:12 安全输入 active mask
 * 19:16 motor_state     (保持)
 * 23:20 safety_state    (保持)
 * 27:24 安全输入 valid  mask
 * 31:28 应用安全输入语义版本(1=本布局; 0=旧记录未带版本; 其他=未知) */
#define BBFL_SHIFT_FAULT        0u
#define BBFL_SHIFT_LEVEL        8u
#define BBFL_SHIFT_ACTIVE       12u
#define BBFL_SHIFT_MOTOR        16u
#define BBFL_SHIFT_SAFETY       20u
#define BBFL_SHIFT_VALID        24u
#define BBFL_SHIFT_SCHEMA       28u

#define BBFL_NIBBLE             0x0Fu
#define BBFL_BYTE               0xFFu
#define BBFL_FAULT_MASK         ((rt_uint32_t)BBFL_BYTE   << BBFL_SHIFT_FAULT)
#define BBFL_LEVEL_MASK         ((rt_uint32_t)BBFL_NIBBLE << BBFL_SHIFT_LEVEL)
#define BBFL_ACTIVE_MASK        ((rt_uint32_t)BBFL_NIBBLE << BBFL_SHIFT_ACTIVE)
#define BBFL_MOTOR_MASK         ((rt_uint32_t)BBFL_NIBBLE << BBFL_SHIFT_MOTOR)
#define BBFL_SAFETY_MASK        ((rt_uint32_t)BBFL_NIBBLE << BBFL_SHIFT_SAFETY)
#define BBFL_VALID_MASK         ((rt_uint32_t)BBFL_NIBBLE << BBFL_SHIFT_VALID)
#define BBFL_SCHEMA_MASK        ((rt_uint32_t)BBFL_NIBBLE << BBFL_SHIFT_SCHEMA)

#define BBFL_SCHEMA_CURRENT     1u          /* 新黑匣子帧写这个值 */
#define BBFL_SCHEMA_LEGACY      0u          /* 旧记录: 从未带版本字段 */

/* flags 打包/解包(只移位与掩码, 不做任何判定) */
#define BBFL_PACK(v, shift)     (((rt_uint32_t)(v) & BBFL_NIBBLE) << (shift))
#define BBFL_PACK_FAULT(v)      (((rt_uint32_t)(v) & BBFL_BYTE)   << BBFL_SHIFT_FAULT)
#define BBFL_UNPACK(flags, shift) \
    ((rt_uint32_t)(((rt_uint32_t)(flags) >> (shift)) & BBFL_NIBBLE))

typedef enum
{
    BB_SAI_SCHEMA_V1 = 0,       /* 按本文件定义解释 */
    BB_SAI_SCHEMA_LEGACY,       /* 版本 0: bit8 的 ESTOP/ENN 角色与活动/有效集合**不可追认** */
    BB_SAI_SCHEMA_UNKNOWN       /* 其他值: 只保留 raw, 绝不默认成版本 1 */
} bb_sai_schema_t;

const char *bb_sai_schema_name(bb_sai_schema_t s);

/* 一条记录的语义版本判定。实现在 blackbox.c, 与 dump 使用同一生产代码。 */
bb_sai_schema_t blackbox_sai_schema(rt_uint32_t flags);

/* 按版本 1 解释出的三段值; 仅在 schema==V1 时才有意义。 */
rt_uint32_t blackbox_sai_levels(rt_uint32_t flags);      /* bit11:8 原始电平 */
rt_uint32_t blackbox_sai_active(rt_uint32_t flags);      /* bit15:12 */
rt_uint32_t blackbox_sai_valid(rt_uint32_t flags);       /* bit27:24 */

/* 单路输入在本条记录里的可读性(供输出区分"禁用"与"读失败")。 */
typedef enum
{
    BB_SAI_READ_VALID = 0,      /* 活动且本帧读到 */
    BB_SAI_READ_INVALID,        /* 活动但本帧没读到(解析失败/非法读数) */
    BB_SAI_READ_DISABLED        /* 配置里就不活动 */
} bb_sai_read_t;

bb_sai_read_t blackbox_sai_read_state(rt_uint32_t flags, rt_uint32_t bit);

#endif /* SAFETY_IO_SEMANTICS_H */
