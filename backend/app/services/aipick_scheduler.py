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
from ..services.cache_store import store

log = logger.get_logger(__name__)

AIPICK_DIR = "/opt/kuaixuan/aipick"
VENV_PY = "/opt/kuaixuan-venv/bin/python"
# 测试机 venv 路径不同(2026-08-18): 生产 /opt/kuaixuan-venv, 测试机 /opt/bid-venv
if not os.path.exists(VENV_PY):
    VENV_PY = "/opt/bid-venv/bin/python3"

# 任务窗口(分钟): (名称, 开始mm, 结束mm, [命令参数...])
_TASKS = [
    # 9:26:30-9:29:30 采集 + 预测(2026-08-18 主人要求: 9:25 竞价结束后 2-3 分钟内出预测;
    # 预测约 10-20 秒, 9:27 采完立即用昨日模型预测当日涨停概率, 9:30 前可看)
    ("aipick_collect", 9 * 60 + 26, 9 * 60 + 30, [os.path.join(AIPICK_DIR, "scripts", "collector.py")]),
    ("aipick_predict", 9 * 60 + 27, 9 * 60 + 31, [os.path.join(AIPICK_DIR, "scripts", "predict_daily.py")]),
    # 15:04:30-15:06:30 打标签
    ("aipick_label", 15 * 60 + 4, 15 * 60 + 7, [os.path.join(AIPICK_DIR, "scripts", "collector.py"), "--label"]),
    # 18:59:30-19:01:30 只训练(预测已挪到 9:27 竞价后; 模型次日生效)
    ("aipick_train", 18 * 60 + 59, 19 * 60 + 2, [os.path.join(AIPICK_DIR, "scripts", "train_model.py")]),
]

# 已执行标记(进程内), 防同一窗口重复
_done_flags = {}


def _is_trade_day(g):
    """周一~周五"""
    return g.tm_wday < 5


def _run_script(script):
    """subprocess 调用 aipick 脚本(超时 180s), 日志记录输出尾部"""
    if not os.path.exists(script):
        log.warning("aipick 脚本不存在: %s (请先部署 /opt/kuaixuan/aipick)", script)
        return False
    cmd = [VENV_PY, script]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        tail = (p.stdout or "").strip().splitlines()
        tail = " | ".join(tail[-3:]) if tail else ""
        if p.returncode == 0:
            log.info("aipick 执行成功 %s -> %s", os.path.basename(script), tail)
            return True
        log.error("aipick 执行失败 %s rc=%s err=%s", os.path.basename(script), p.returncode,
                  (p.stderr or "").strip()[-300:])
        return False
    except subprocess.TimeoutExpired:
        log.error("aipick 执行超时 %s", os.path.basename(script))
        return False
    except Exception as e:
        log.error("aipick 执行异常 %s err=%s", os.path.basename(script), e)
        return False


def _run_task(name, scripts):
    """执行一个任务(可多脚本), 跨进程 setnx 去重防多 worker 重复"""
    # 跨进程锁: 当日只执行一次(CacheStore setnx, 锁 12h)
    if not store.setnx("aipick:" + name + ":" + time.strftime("%Y-%m-%d"), "1", 12 * 3600):
        log.info("aipick %s 今日已执行过, 跳过", name)
        return
    for script in scripts:
        _run_script(script)


def _scheduler_loop():
    while True:
        try:
            g = time.gmtime(time.time() + 8 * 3600)
            hm = g.tm_hour * 60 + g.tm_min
            if _is_trade_day(g):
                for name, start, end, scripts in _TASKS:
                    key = name + ":" + str(g.tm_mday)
                    if start <= hm <= end and _done_flags.get(key) is not True:
                        _done_flags[key] = True
                        log.info("aipick 任务触发: %s (%02d:%02d)", name, g.tm_hour, g.tm_min)
                        _run_task(name, scripts)
        except Exception as e:
            log.error("aipick 调度异常 err=%s", e)
        time.sleep(20)


def trigger_after_bid_snapshot():
    """9:25 竞价快照落库后由 auction_snapshot 立即触发(2026-08-18 主人要求:
    拿到竞价数据后立刻采集+预测, 不等 9:27 轮询窗口) — 后台线程执行, 不阻塞采集主循环"""
    def _wrapped():
        try:
            _run_task("aipick_collect", [os.path.join(AIPICK_DIR, "scripts", "collector.py")])
            _run_task("aipick_predict", [os.path.join(AIPICK_DIR, "scripts", "predict_daily.py")])
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
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    log.info("AI 竞价选股调度已启动(9:25触发预测 / 9:27采集 / 15:05打标签 / 19:00训练)")
