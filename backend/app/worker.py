# -*- coding: utf-8 -*-
"""
kx-worker 独立进程: 快照采集调度 + 尾盘推送 + 异步任务队列消费
================================================================
背景: 调度任务原本内嵌在 web 进程(FastAPI startup), 单进程架构下
  9:25 高峰采集与 API 请求抢资源, web 重启即中断采集。
目标: 调度与 web 解耦, 独立 systemd 单元管理, 可单独升级/重启。

启动: python -m app.worker
systemd: kx-worker.service (与 kx-web 同机)

职责:
  1. auction_snapshot 多时点快照调度(9:15/9:20/9:24/9:25 + 最后一秒采样 + 日终归档)
  2. wpqc_push 尾盘竞价抢筹推送(14:57)
  3. task_queue 异步任务消费(Phase1 建立框架, save_batch 默认仍同步)
"""
import json
import time

from .core import logger
from .db import database
from .services import auction_snapshot, wpqc_push, aipick_scheduler, concept_refresh, ladder_daily, stock_temper

log = logger.get_logger(__name__)


# ---------- 异步任务消费 ----------
def _handle_save_batch(payload):
    """异步落库(Phase1 预留: save_batch 默认仍同步, 切换 async 后由 worker 执行)"""
    from .services import history
    return history.save_batch(
        payload["user_id"], payload["action"], payload["result"], payload["f"])


_TASK_HANDLERS = {
    "save_batch": _handle_save_batch,
}


def consume_loop():
    """轮询 task_queue, 处理 pending 任务(1s 间隔, 批量 20)"""
    while True:
        try:
            tasks = database.get_pending_tasks(20)
            for t in tasks:
                try:
                    handler = _TASK_HANDLERS.get(t["type"])
                    if handler:
                        handler(json.loads(t["payload"]))
                        database.mark_task_done(t["id"], failed=False)
                        log.info("任务完成 id=%s type=%s", t["id"], t["type"])
                    else:
                        log.warning("未知任务类型 id=%s type=%s", t["id"], t["type"])
                        database.mark_task_done(t["id"], failed=True)
                except Exception as e:
                    log.error("任务处理失败 id=%s type=%s err=%s", t["id"], t["type"], e)
                    database.mark_task_done(t["id"], failed=True)
        except Exception as e:
            log.warning("队列消费异常 err=%s", e)
        time.sleep(1)


def main():
    logger.setup_logging()
    database.init_db()
    log.info("=== kx-worker 启动 ===")
    # 调度线程(原 web startup 逻辑整体搬移, 与 web 解耦)
    auction_snapshot.start_scheduler()
    wpqc_push.start_scheduler()
    aipick_scheduler.start_scheduler()
    concept_refresh.start_scheduler()  # 盘中每30分钟从开盘啦刷新竞价异动股票概念并写库
    ladder_daily.start_scheduler()     # 交易日 15:30 盘后生成连板天梯 PNG
    stock_temper.start_scheduler()     # 盘后落库涨停/炸板(股性数据源): 18:30 窗口 + 09:00 盘前补救
                                       # (2026-09-13 P1: 原 15:30 早于上游发布时刻, 该任务从未成功过)
    log.info("快照采集 + 尾盘推送 + AI竞价选股调度 + 盘中概念刷新 + 连板天梯盘后生成 + 股性数据落库已启动")
    # 主线程阻塞消费队列
    consume_loop()


if __name__ == "__main__":
    main()
