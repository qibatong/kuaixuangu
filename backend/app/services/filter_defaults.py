# -*- coding: utf-8 -*-
"""
筛选默认参数单一真相源 (v4.11.46)
=================================================================================
背景(2026-09-24 主人拍板修): 本仓曾有**四份**彼此独立的筛选默认值 ——

  | 位置 | 键数 | 含 scoreFloor |
  | --- | --- | --- |
  | api/admin.DEFAULT_FILTERS_DEFAULT | 10 | 是(真相源) |
  | services/system_batch.DEFAULT_FILTERS_DEFAULT | 9  | **否** |
  | services/picker/lock._FILTER_DEFAULTS | 10 | 是(兜底) |
  | services/auto_apply._get_system_filter | -  | 是(直接读 admin) |

只有 system_batch 那份缺 scoreFloor。后果实测(测试机 47.99.153.123, 2026-09-24):

    settings.default_filters          scoreFloor = 60   (管理员设定)
    admin.get_default_filters()       scoreFloor = 60   (首页左视图)
    auto_apply._get_system_filter()   scoreFloor = 60   (9:26 自动应用)
    system_batch._system_filter()     无此键
    → 经 lock.to_picker_filters 落定   scoreFloor = 50   (硬编码兜底)

⇒ 同一时刻同一份 9:25 快照: **系统批次 64 只 vs 首页左视图 27 只**,
  与 system_batch docstring 声称的"与首页左视图完全一致"不符。

🔴 根因不在数值抄错, 而在**合并逻辑的白名单**:

    for k, v in cfg.items():
        if k in merged:        # ← merged 来自本地副本(9 键) → settings 的
            merged[k] = v      #    scoreFloor(60) 根本不进白名单 → 静默丢弃

所以修复不能只"补一个键"——必须让**白名单本身**来自唯一真相源, 否则下次
admin 加新参数时, 同一个坑会原样复现一次(而测试还测不出来: validate_filters
对缺键有默认值, 输出上完全看不出键是否存在)。

本模块把四份归口为一份。**新增/修改默认筛选参数只改 FILTER_DEFAULTS。**
=================================================================================
"""
from ..core import logger
from . import settings

log = logger.get_logger(__name__)


# ---------------------------------------------------------------------------
# 全局默认筛选参数(管理员可调: PUT /api/admin/defaults → settings 表 key=default_filters)
#
# 语义(2026-08-25 起为正逻辑): limitUp / stSuspend = True → "只看这类票",
#   False → "剔除这类票"。默认 False 等价旧默认("勾上=剔除"), 过滤结果一致但 UI 直觉正确。
# ---------------------------------------------------------------------------
FILTER_DEFAULTS = {
    # 🔴 2026-10-03 主人反馈"名单里很多跌停票" ⇒ 补**竞价涨幅下界**（此前只有上界 bidGt，
    #    低开/跌停票照样进名单；框架 filter.py 早有 bidLt 语义但默认值未进真相源 ⇒ 各消费方看不见）。
    #    取值 2.0 有实测支撑：100 日逐日 top30 封板率 无下界 27.87% → 下界 2% **32.61%**；
    #    分桶涨停率 ≤-9% 1.66% / 0~2% 1.42% / 2~5% 7.46% / 5~8% 21.77% / >8% 40.77%。
    #    管理员可调（PUT /api/admin/defaults），负数=允许低吸（不建议）。
    # 🔴 2026-10-06 主人指令：**撤下界**（0 = 不限）—— 允许低开票进名单（低开反包也是涨停来源）。
    "bidLt": 0.0,
    # 🔴 2026-10-03 主人指令"不需要凑满30只，按封板率越高越好展示" ⇒ 主阈值改为
    #    **竞价涨幅 ≥ 该板块涨停幅度 × bidLtRatio**（主板 8% / 创业科创 16% / 北交所 24%）。
    #    实测（100 日，全名单不凑数）：≥80% ⇒ 11 只/日、封板率 **68.4%**（top5 70.7%）；
    #    对比统一 6% 阈值：主板 44.0% 而创业科创仅 12.2%（统一阈值对 20% 板不公平）。
    #    bidLt(绝对下界 2%) 保留为兜底；bidLtRatio=0 表示只用绝对下界。
    # 🔴 2026-10-06 主人指令：**撤掉"涨停幅度×80%"这条主阈值**（0 = 不用）。
    "bidLtRatio": 0.0,
    # 🔴 2026-10-06: `bidGt` 语义改为**附加上限** —— 按板块自适应（bidGtRatio>0）生效时，
    #    0 = 不附加（上界完全由"板块涨停幅度×bidGtRatio"决定）；>0 则取两者较小值，
    #    保留"管理员/用户进一步收紧"的能力（否则该输入框会变成静默失效的死开关）。
    "stSuspend": False, "limitUp": False, "bidGt": 0.0,
    # ★ 2026-10-06 主人要「榜单完整度」：竞价涨幅**上界按板块自适应**。
    #    bidGtRatio > 0 时，上界 = 该板块涨停幅度 × bidGtRatio
    #      （主板 10×1.04 = **10.4%**、创业/科创 20×1.04 = 20.8%、北交所 30×1.04 = 31.2%），
    #      此时 `bidGt`（10.4）仅作兜底；置 0 则退回固定上界 `bidGt`。
    #    🔴 动因（实测）：统一上界会把 20%/30% 板"竞价 10%~20%"的强势票整片截断 ——
    #      2026-09-30 善水科技 301190（创业板）竞价 +10.89%、模型概率 0.977、当日涨停，
    #      却被统一 10.4% 挡在名单外（10.89 > 10.4）。
    #    板块涨停幅度走 `auction_snapshot.zt_limit_pct` 单一真相源（300/301/688/689→20%、920/8/4→30%）。
    "bidGtRatio": 1.04,
    "probLt": 50.0, "confLt": 50.0,
    "floatMvFloor": 30.0, "floatMvGt": 1000.0, "priceGt": 300.0,
    # 🔴 2026-10-02 主人指令: 竞价金额限制**取消**（0 = 不限）；同时竞价涨幅上限 7 → 10%
    #    动因: 目标锁定"命中涨停为主" —— 金额门槛会把高命中小票挡在名单外(实测候选/日 646→1466)。
    #    与 scripts/aipick/predict_daily.py 的 DEFAULT_BID_AMT_MIN/CHG_MAX、前端 AipickReport 默认**同批发布**。
    "bidAmtFloor": 0.0,
    "scoreFloor": 50.0,      # 2026-09-20 主人拍板: 评分低于 50 分的票不显示(全站默认)
    # ---- ZH 竞价选股策略(2026-09-29, 见 services/picker/zh.py)----
    # 竞价类参数, 只被 ZH 策略消费; 放在这里是为了"管理员可调 + 单一真相源"
    # (与 bidLt 同一模式: 系统口径去掉 markets 后必须与全局默认完全一致, 有 parity 测试盯着)
    "zhBidGt": 3.0,          # 高开下限(%)
    "zhVolPctFloor": 5.0,    # 竞价放量占昨量下限(%) —— 主人实测原区间偏窄, 可放宽到 3
    "zhVolPctGt": 10.0,      # 占昨量上限(%) —— 可放宽到 15
    "zhZtGeneDays": 120,     # 涨停基因回看交易日数
    "zhZtGeneMin": 1,        # 窗口内最少涨停次数
}


# ---------------------------------------------------------------------------
# 系统选股的市场范围(与首页左视图一致; 不读用户自定义 filter_prefs)
#
# 🔴 口径必须是**小写** hs/cyb/kcb/bj —— scorer._in_markets / picker.filter.in_markets
#    按代码前缀匹配小写键, 传大写 ["SH","SZ","BJ"] 会让沪深创科全部返回 False
#    → 名单恒空(2026-09-08 P4 实测事故: 批次 #1578 count=0)。
#    2026-09-29 主人拍板「**北交所纳入**」: 追加 bj(与前端 defaultFilterSettings
#    和市场白名单同步; 前端默认值与系统口径必须一致, 有 parity 测试盯着)。
#
# ⚠️ ALL_MARKETS 是"**全市场**"(兜底/预热/全市场行情拉取)的唯一真相源 ——
#    任何 `market_fs(["hs","cyb","kcb"])` 的字面量副本都是隐患: 2026-09-29 纳入
#    北交所时, 仓库里同时存在 6 处副本(行情兜底/预热/预计算/两个 picker 源),
#    漏改任何一处都会出现"名单里有北交所、行情却永远取不到"的静默缺数。
# ---------------------------------------------------------------------------
ALL_MARKETS = ("hs", "cyb", "kcb", "bj")
SYSTEM_MARKETS = list(ALL_MARKETS)


def resolved_defaults() -> dict:
    """全局默认筛选参数 = FILTER_DEFAULTS 与 settings 表 default_filters 的合并。

    合并白名单**以 FILTER_DEFAULTS 的键集为准**(唯一真相源), 而不是各调用点
    自己抄的那份 —— 从根上消灭"settings 里有值、却被本地键集丢掉"这类事故。

    Returns:
        新的 dict(调用方可自由修改, 不会污染 FILTER_DEFAULTS); 键集 == FILTER_DEFAULTS。
    """
    merged = dict(FILTER_DEFAULTS)
    cfg = settings.get("default_filters")
    if isinstance(cfg, dict):
        unknown = []
        for k, v in cfg.items():
            if k in merged:
                merged[k] = v
            else:
                unknown.append(k)
        if unknown:
            # 管理员手改 DB / 老版本残留字段会走到这里。不致命, 但必须可观测:
            # 否则"我明明在后台配了却没生效"又要靠人肉复算才能发现。
            log.warning("default_filters 含未知键(已忽略): %s", ",".join(sorted(unknown)))
    return merged


def system_filters() -> dict:
    """"系统统一筛选标准" = 全局默认 + 系统市场范围。

    供**两条锁仓链路**共用, 保证与首页左视图同口径:
      - services.system_batch  (9:25 定格落库后自动跑的系统批次 / 历史回看名单)
      - services.auto_apply    (9:26 自动应用, 系统筛选一次推给所有用户)

    用 setdefault 而非强制覆写: 当前 FILTER_DEFAULTS 不含 markets 且 admin 的
    PUT 校验不接受未知字段, 故二者等价; 保留 setdefault 是为了将来管理员若要
    配置 markets 时不会静默失效。

    Returns:
        新的 dict, 含 markets=["hs","cyb","kcb"]。
    """
    f = resolved_defaults()
    f.setdefault("markets", list(SYSTEM_MARKETS))
    return f
