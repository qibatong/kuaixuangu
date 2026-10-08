# -*- coding: utf-8 -*-
"""
AI 竞价选股调度器 (2026-08-18 主人要求: 定时功能与系统绑定, 不依赖 WorkBuddy/本地电脑)
=====================================================================================
将原本跑在本地 WorkBuddy 自动化的 3 个 AI 竞价任务迁移到 kx-worker 服务内调度:

  1. 9:27   采集   -> collector.py            抓取当日 9:25 竞价快照写入 aipick.db
  2. 15:05  打标签 -> collector.py --label    收盘回填涨停标签(是否涨停/收盘涨幅)
  3. 19:00  训练+预测 -> train_model.py + predict_daily.py 重训练模型 + 生成次日预测

实现: 独立线程每 20s 轮询, 工作日 + 目标时间窗口内执行一次(跨进程 setnx 去重)。
aipick 独立代码部署于 /opt/kuaixuan/aipick/ (不在快选 git 仓库, 独立小项目)。
"""
import os
import subprocess
import threading
import time

from ..core import logger
from ..core import trade_calendar as tc
from ..services.cache_store import store

log = logger.get_logger(__name__)

AIPICK_DIR = "/opt/kuaixuan/aipick"
VENV_PY = "/opt/kuaixuan-venv/bin/python"
# 测试机 venv 路径不同(2026-08-18): 生产 /opt/kuaixuan-venv, 测试机 /opt/bid-venv
if not os.path.exists(VENV_PY):
    VENV_PY = "/opt/bid-venv/bin/python3"

# 任务窗口(分钟): (名称, 开始mm, 结束mm, [(脚本, [参数...]), ...])
_TASKS = [
    # 9:26:30-9:29:30 采集 + 预测(2026-08-18 主人要求: 9:25 竞价结束后 2-3 分钟内出预测;
    # 预测约 10-20 秒, 9:27 采完立即用昨日模型预测当日涨停概率, 9:30 前可看)
    ("aipick_collect", 9 * 60 + 26, 9 * 60 + 30, [(os.path.join(AIPICK_DIR, "scripts", "collector.py"), [])]),
    # 2026-09-25 LightGBM 平行链路: 与 XGBoost **同任务名/同窗口**, 只是按顺序多跑一遍并写
    #   独立目录 output/lgb。
    #   🔴 刻意**不新建** `aipick_predict_lgb` 这类独立任务名 —— 上方的「等 9:25 定格落库」
    #      守卫是按**任务名白名单**(`_AIPICK_NEED_SNAPSHOT`)放行的; 新建任务名就会绕过守卫,
    #      让 LGB 预测在定格落库前抢跑 → 静默回退猫爪自拉(非权威同源), 且 `_run_task` 的
    #      setnx 已烧掉当日唯一那次。挂进已有任务名则**自动继承**与 XGB 完全相同的时序语义。
    #   `_run_script` 逐个执行且各自捕获异常/超时 ⇒ 前一个脚本失败不影响后一个(故障隔离)。
    ("aipick_predict", 9 * 60 + 27, 9 * 60 + 31, [
        (os.path.join(AIPICK_DIR, "scripts", "predict_daily.py"), []),
        (os.path.join(AIPICK_DIR, "scripts", "predict_daily.py"),
         ["--algo", "lgbm", "--out-dir", os.path.join(AIPICK_DIR, "output", "lgb")]),
    ]),
    # 15:04:30-15:06:30 打标签(注意: 旧写法把 --label 当脚本路径, 参数从未生效, 2026-08-30 修复)
    ("aipick_label", 15 * 60 + 4, 15 * 60 + 7, [(os.path.join(AIPICK_DIR, "scripts", "collector.py"), ["--label"])]),
    # 15:07:30-15:13:30 补生成缺失报告(2026-08-30 主人反馈: 当天没跑 9:27 预测 → 历史回看缺失)
    # backfill 从快照库取最近 30 个交易日, 缺 predictions_{d}.json 就用 9_25 快照补生成, 保证复盘完整
    # 🔴 2026-09-25: **刻意不给 LightGBM 挂 backfill**(主人决定「清空回补, 只留实时累积」)。
    #    原因见 lgbm-deploy/DIAGNOSIS-bid_turnover.md: backfill 在盘后/历史回溯执行,
    #    取数落到 `fetch_from_kuaixuan` 的猫爪补字段路径, 而 `bid_turnover` 被塞的是猫爪
    #    `turnover_rate_f`——它是「截至目前」的全天换手率: 9:25 竞价时点 ≈0.006, 盘后 ≈2.5~2.9,
    #    **相差 250~400 倍**。喂给模型后概率整体塌到 ~0.003, 页面只剩 0~3 只票(实测用未改动的
    #    XGB 链路复现: 同一份 09-24 报告, 早盘 10 只 → 盘后重跑 3 只)。
    #    XGB 历史报告当年就是这么被"污染"的(线上 08-17/08-24 报告 turnover=1.75 即盘后回补痕迹)。
    #    ⇒ 若给 LGB 挂上 backfill, 每天盘后都会把错量纲报告重新灌回 output/lgb, 用户会误判
    #      「LGB 比 XGB 差得多」。故 LGB 历史**只由 9:25 实时链路自然积累**。
    #    代价: 实时任务某天没跑成 → 该日在 LGB 回看页永久缺失(不能靠 backfill 补)。
    #    待 bid_turnover 口径修好后, 再评估是否补挂。
    ("aipick_backfill", 15 * 60 + 7, 15 * 60 + 14, [
        (os.path.join(AIPICK_DIR, "scripts", "predict_daily.py"), ["backfill", "30"]),
    ]),
    # 18:59:30-19:01:30 只训练(预测已挪到 9:27 竞价后; 模型次日生效)
    # 2026-09-25: LGB 训练同窗口串行(本模块单线程轮询, 两个训练不会并发抢 CPU);
    #   产物 models/model_lgb.txt + models/model_meta_lgb.json + output/lgb/train_report.json,
    #   **绝不写** output/train_report.json(该文件被 scripts/aipick/backtest.py 消费)。
    # 2026-10-03 上线：9:28 预测后统一落库四线名单（pick_daily；含可买性分级/模拟成交）
    ("aipick_pickdaily", 9 * 60 + 28, 9 * 60 + 32, [
        (os.path.join(AIPICK_DIR, "scripts", "pick_daily.py"),
         ["--db", os.path.join(AIPICK_DIR, "scripts", "data", "aipick.db"), "--lines", "xgb,lgb,zh"]),
    ]),
    # 2026-10-03 上线：盘后 15:15 留存当日分钟线（**仅最新交易日可得、过期不可追回**；供卖出规则精确回放）
    ("aipick_minute", 15 * 60 + 15, 15 * 60 + 20, [
        (os.path.join(AIPICK_DIR, "scripts", "capture_minute.py"),
         ["--db", os.path.join(AIPICK_DIR, "scripts", "data", "aipick.db")]),
    ]),
    # ==================== 影子链路已于 2026-10-06 整体下线（主人决定）====================
    # 原 4 项调度（aipick_shadow 9:27 / shadow_train 19:15 / shadow_gate 19:25）全部移除：
    #   ① fetch_auc     抓当日 9:25 全市场竞价特征 → aipick.db 的 auc_open 表
    #   ② picks         影子出名单 → pick_daily_shadow
    #   ③ shadow_train  训练影子候选 → models/versions/
    #   ④ shadow_gate   闸门判定 → 达标自动晋级（原子替换 model_lgb.txt）
    #
    # 下线理由（2026-10-06 排查结论）：
    #   · 该系统 2026-10-03 早上搭好后**恰逢国庆休市**，调度从未执行过；且因「存在影子指针
    #     就跳过重训」的冻结规则 + OOS 校验（窗口起点必须晚于候选训练末端），
    #     开工后也会**永久停在 v_live_start**，既不出候选也不晋级。
    #   · 与主链路是**两套模型体系**：影子系统管 model_lgb.txt（火眼链路），
    #     而评分 AI 因子走 ai_predict → model_xgb.json ⇒ 影子系统从未影响主链路。
    #   · 主人决定：不再需要该机制。
    #
    # ⚠️ 已同步移除的关联物：
    #   · scripts/aipick/shadow.py → scripts/aipick/_deprecated/（保留可回查，不再被调度）
    #   · _AIPICK_NEED_SNAPSHOT 白名单里的 "aipick_shadow"
    #   · 后端 chaozhi.load_shadow() 及超智页的「影子模型」展示块
    #   · 生产 models/versions/ · current.json · shadow_freeze.json → 归档至 models/_deprecated_shadow/
    # ⚠️ 数据库表 pick_daily_shadow / shadow_gate / auc_open **保留不删**（仅不再写入），
    #    如需彻底清理请先确认无其他消费者。
    ("aipick_train", 18 * 60 + 59, 19 * 60 + 2, [
        (os.path.join(AIPICK_DIR, "scripts", "train_model.py"), []),
        (os.path.join(AIPICK_DIR, "scripts", "train_lgbm.py"), []),
    ]),
]

# 已执行标记(进程内), 防同一窗口重复
_done_flags = {}


def _specs_of(name):
    """按任务名取 `_TASKS` 里的脚本清单（**唯一事实来源**）。

    🔴 为什么必须有（2026-09-25 LightGBM 平行链路踩坑）：
      `trigger_after_bid_snapshot()` 是「9:25 定格落库后立即触发」的快路径，原先**写死了**
      XGB 那一条脚本（`[(predict_daily.py, [])]`）。而 `_run_task()` 内部
      `store.setnx("aipick:" + name + ":" + 日期)` 会烧掉**当日唯一那把锁** ⇒ 只要快路径
      先跑（正常交易日它一定先跑），9:27 轮询窗口里的同名任务就会直接
      「今日已执行过, 跳过」，**被写死的清单之外的脚本（LightGBM）永远不会执行**，
      且只在日志留一行 INFO，不报错、不告警 —— 表现为「LGB 页面永远是空的」。
      ⇒ 统一改为从 `_TASKS` 取清单，杜绝两处清单各自漂移。

    Args:
        name: `_TASKS` 里的任务名。
    Returns:
        该任务的 `[(脚本路径, [参数...]), ...]`；任务名不存在时返回 `[]`。
    """
    for n, _, _, specs in _TASKS:
        if n == name:
            return specs
    return []


# ---- 采集/预测的**定格就绪门**(2026-09-24, 与「定格推迟」配套) ----
# 背景: `auction_snapshot` 推迟到「拿到猫爪数据再定格」后, 9_25 **落库时刻**从
#   09:25:2x 移到 **09:26:3x~09:26:4x**(定格首采 `_BID25_FREEZE_SEC=09:26:30`
#   + 全市场拉取 ≈8~15s; 重采截止 `_BID25_RETRY_UNTIL=09:27:30`)。
# 🔴 而本模块 `aipick_collect` 的窗口起点是 **09:26:00**, 轮询间隔 20s ⇒ 首个轮询点落在
#   09:26:00~09:26:19, **与落库时刻重叠 → 约五成概率抢跑**。抢跑代价(已读代码坐实):
#   `collector.fetch_from_kuaixuan()` 读 `snapshot_bid` 得 0 行 → `return None` →
#   静默回退 `fetch_market()`(猫爪自拉, **非**"权威采集同源") —— 且 `_run_task` 的
#   `store.setnx` 已烧掉当日**唯一**那次 ⇒ 全天 AI 预测的输入集与设计不符, 且只在
#   collector 的 stdout 留一行"回退猫爪自拉"。
# ⇒ 故采集/预测在窗口内**先等当日 9_25 落库**。守卫只加在真正依赖定格的这两个任务上。
#
# 硬兜底: 到 `_AIPICK_READY_FALLBACK_HM`(9:29) 即使仍无定格也放行 —— 定格整点缺失是
#   独立故障(已有 `_check_system_batch` 补跑+飞书告警链路), 不能让 AI 侧连带"当天彻底不跑"。
# 2026-10-03: 影子链路(aipick_shadow)同样必须等 9:25 定格落库后再跑 —— 它读的是
#   features(11 维) + auc_open(当日竞价特征)，抢跑会拿到空快照 ⇒ 名单为空。
_AIPICK_NEED_SNAPSHOT = ("aipick_collect", "aipick_predict")
_AIPICK_READY_FALLBACK_HM = 9 * 60 + 29         # 09:29


def _is_trade_day(g):
    """是否交易日 = 周一~周五 **且非法定休市日**。

    ★ 2026-09-25（中秋节 · 星期五）幽灵报告复盘新增：原实现只有 `g.tm_wday < 5`，
      没有节假日日历 ⇒ 法定假日被当交易日，AI 链路照跑、产出 30 只假名单。
      现改用 `core/trade_calendar`（上交所官方休市表）。
      表过期时 fail-open 退化为「只判周几」并打 ERROR 提醒补表 —— 绝不误拦真实交易日。

    g 必须是**北京时间** struct_time（调用方已做 +8h 处理）。
    """
    return tc.is_trade_day_of(g)


def _bid25_landed():
    """当日 9_25 定格的**只读**落库探测。

    单一入口(供本模块两处竞态守卫共用), 口径与选股闸门的快照维同源
    (`auction_snapshot.has_today_snapshot`), 不引入第二套判据。
    异常按**未落库**处理(保守): 宁可晚一轮, 不可抢跑。
    """
    try:
        from . import auction_snapshot
        return bool(auction_snapshot.has_today_snapshot())
    except Exception as e:                                        # noqa: BLE001
        log.warning("9_25 定格落库探测失败(按未落库处理) err=%s", e)
        return False


def _aipick_ready(name, hm):
    """该任务当前是否具备开跑条件(当日 9_25 定格已落库)。

    只对 `_AIPICK_NEED_SNAPSHOT` 里的任务设门 —— 它们的脚本从 `snapshot_bid` 的
    9_25 行取数(见 `scripts/aipick/collector.py:fetch_from_kuaixuan`)。其余任务
    (打标签 / 补生成 / 训练)不依赖当日定格, 一律放行。

    ★ 判据只查**存在性**(`has_today_snapshot`), **不要求"量比已就绪"**: collector 的
      SELECT 只取 `code,name,bid_change,bid_amt,float_mv`, 与 `auc_vol_ratio`/`auc_main_net`
      无关 ⇒ 第一枪(可能还缺迟到列)落库就足够。要求过高会把 AI 侧无谓地推后。

    Args:
        name: `_TASKS` 里的任务名。
        hm: 当日分钟数(hour*60+min)。
    Returns:
        True 可开跑; False 继续等下一轮(调用方 **不** 置 `_done_flags`, 故会重试)。
    """
    if name not in _AIPICK_NEED_SNAPSHOT:
        return True
    if hm >= _AIPICK_READY_FALLBACK_HM:          # 硬兜底, 见上方注释
        return True
    if _bid25_landed():
        return True
    log.info("aipick %s 等待当日 9_25 定格落库 (%02d:%02d)", name, hm // 60, hm % 60)
    return False


def _system_batch_check_due(hm):
    """`_check_system_batch` 是否该执行(抽成纯判据便于断言, 同 `_netfill_due` 的做法)。

    窗口 9:26-9:28; **9:27 起无条件执行** —— "整整一分钟都没有定格"正是本检查要告警的场景,
    不能因为守卫而永不检查; 9:26 那一分钟则必须先等定格落库, 否则会误报飞书 + 拿空快照补跑批次。

    Args:
        hm: 当日分钟数(hour*60+min)。
    Returns:
        True 该执行检查。
    """
    if not (9 * 60 + 26 <= hm <= 9 * 60 + 28):
        return False
    return hm >= 9 * 60 + 27 or _bid25_landed()


def _run_script(script, args=None):
    """subprocess 调用 aipick 脚本(超时 180s), 日志记录输出尾部
    args: 附加命令行参数(2026-08-30 修复: 旧实现把参数误当脚本路径)"""
    if not os.path.exists(script):
        log.warning("aipick 脚本不存在: %s (请先部署 /opt/kuaixuan/aipick)", script)
        return False
    cmd = [VENV_PY, script] + list(args or [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        tail = (p.stdout or "").strip().splitlines()
        tail = " | ".join(tail[-3:]) if tail else ""
        if p.returncode == 0:
            log.info("aipick 执行成功 %s %s -> %s", os.path.basename(script), args or "", tail)
            return True
        log.error("aipick 执行失败 %s %s rc=%s err=%s", os.path.basename(script), args or "",
                  p.returncode, (p.stderr or "").strip()[-300:])
        return False
    except subprocess.TimeoutExpired:
        log.error("aipick 执行超时 %s", os.path.basename(script))
        return False
    except Exception as e:
        log.error("aipick 执行异常 %s err=%s", os.path.basename(script), e)
        return False


def _run_task(name, specs):
    """执行一个任务(可多脚本), 跨进程 setnx 去重防多 worker 重复
    specs: [(脚本路径, [参数...]), ...]"""
    # 跨进程锁: 当日只执行一次(CacheStore setnx, 锁 12h)
    if not store.setnx("aipick:" + name + ":" + time.strftime("%Y-%m-%d"), "1", 12 * 3600):
        log.info("aipick %s 今日已执行过, 跳过", name)
        return
    for script, args in specs:
        _run_script(script, args)


def _scheduler_loop():
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            hm = g.tm_hour * 60 + g.tm_min
            if _is_trade_day(g):
                for name, start, end, scripts in _TASKS:
                    key = name + ":" + str(g.tm_mday)
                    if start <= hm <= end and _done_flags.get(key) is not True:
                        # 定格就绪门(2026-09-24): 未放行时**不置** _done_flags ⇒ 下一轮(20s)重试。
                        if not _aipick_ready(name, hm):
                            continue
                        _done_flags[key] = True
                        log.info("aipick 任务触发: %s (%02d:%02d)", name, g.tm_hour, g.tm_min)
                        _run_task(name, scripts)
                # 9:26:30-9:28 检查 system_batch 是否落库(2026-08-30 主人要求:
                # 9_25 落库后 system_batch 应已自动存历史回看; 若缺失 → 补跑 + 飞书告警)
                # ★ 2026-09-24: 同一条竞态 —— 本检查**每天只跑一次**(setnx), 而 9_25 落库已推到
                #   09:26:3x~09:26:4x, 若抢在落库前检查 ⇒ 误报「今日系统自动选股批次未生成」并推飞书,
                #   还会**拿着空快照去补跑批次**。故先等落库; 9:27 起无论有无定格都执行
                #   —— "整整一分钟都没有定格"正是本检查要告警的场景, 不能因此永不检查。
                if _system_batch_check_due(hm):
                    _done_flags.setdefault("system_batch_check:" + str(g.tm_mday), False)
                    if not _done_flags["system_batch_check:" + str(g.tm_mday)]:
                        _done_flags["system_batch_check:" + str(g.tm_mday)] = True
                        _check_system_batch()
        except Exception as e:
            log.error("aipick 调度异常 err=%s", e)
        time.sleep(20)


def _check_system_batch():
    """每日 9:26:30 后检查当日 system batch(历史回看自动批次)是否已落库。
    缺失 → 尝试补跑 system_batch + 推送飞书告警(数据源故障时提示人工关注)。
    跨进程 setnx 去重, 防多 worker 重复告警。"""
    today_str = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
    # 当日只检查+告警一次
    if not store.setnx("aipick:system_batch_check:" + today_str, "1", 12 * 3600):
        return
    try:
        from . import system_batch
        if system_batch._has_today_system_batch(today_str, "9_25"):
            log.info("system_batch 检查通过: 今日已落库 %s", today_str)
            return
        log.warning("system_batch 检查失败: 今日未落库 %s, 触发补跑+告警", today_str)
        # 尝试补跑(东财正常时成功; 失败不阻塞告警)
        system_batch.run_system_batch("9_25")
        # 告警: 数据源可能故障或 9_25 快照未采集
        try:
            from . import notify
            notify.send_text(
                "⚠️ 今日 %s 系统自动选股批次未生成(9_25 快照可能未采集或数据源故障)。\n"
                "已尝试后台补跑, 请稍后在「历史回看」确认; 若持续缺失请检查东财接口/快照调度。"
                % today_str,
                # 2026-10-08 主人定名：「网站和 app 名称都是快选股」
                title="快选股·历史回看批次缺失")
        except Exception as e:
            log.error("system_batch 缺失告警推送失败 err=%s", e)
    except Exception as e:
        log.error("system_batch 检查异常 err=%s", e, exc_info=True)


def trigger_after_bid_snapshot():
    """9:25 竞价快照落库后由 auction_snapshot 立即触发(2026-08-18 主人要求:
    拿到竞价数据后立刻采集+预测, 不等 9:27 轮询窗口) — 后台线程执行, 不阻塞采集主循环

    ★ 2026-09-25: 脚本清单**必须走 `_specs_of()`**。原实现写死 XGB 单条，会烧掉当日
      `aipick_predict` 的唯一锁，导致 LightGBM 那条 spec 在 9:27 窗口被
      「今日已执行过, 跳过」——见 `_specs_of()` 的说明。
    """
    def _wrapped():
        try:
            _run_task("aipick_collect", _specs_of("aipick_collect"))
            _run_task("aipick_predict", _specs_of("aipick_predict"))
        except Exception as e:
            log.error("aipick 立即采集/预测异常 err=%s", e)
    threading.Thread(target=_wrapped, daemon=True, name="aipick_snapshot_now").start()
    log.info("aipick 立即采集+预测已触发(9:25 快照落库后)")


def start_scheduler():
    """kx-worker 启动时调用: 启动 AI 竞价采集/打标签/训练预测调度线程"""
    # 进程内标记跨天重置: 每天 00:00 清一次
    def _daily_reset():
        last = None
        while True:
            d = time.strftime("%Y-%m-%d")
            if last != d:
                _done_flags.clear()
                last = d
            time.sleep(300)
    threading.Thread(target=_daily_reset, daemon=True).start()
    # 2026-09-22 v4.11.35: 用户行为记录(login_log / usage_daily)每日 03:30 清理过期数据。
    # 与交易日无关, 所以不能挂在 _scheduler_loop 里(那个循环只在交易日跑任务)。
    def _purge_activity():
        while True:
            try:
                g = time.gmtime(time.time() + 8 * 3600)
                if g.tm_hour == 3 and g.tm_min == 30:
                    from . import activity
                    activity.purge()
                    time.sleep(70)   # 跨过这一分钟, 别在同一天里重复跑
            except Exception as e:
                log.error("行为记录清理异常 err=%s", e)
            time.sleep(20)
    threading.Thread(target=_purge_activity, daemon=True, name="activity_purge").start()
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    log.info("AI 竞价选股调度已启动(9:25触发预测 / 9:27采集 / 15:05打标签 / 19:00训练)")
