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
    "stSuspend": False, "limitUp": False, "bidGt": 7.0,
    "probLt": 50.0, "confLt": 50.0,
    "floatMvFloor": 30.0, "floatMvGt": 1000.0, "priceGt": 300.0,
    "bidAmtFloor": 1000.0,   # 诗人需求: 默认竞价金额下限 1000万(原3000)
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
