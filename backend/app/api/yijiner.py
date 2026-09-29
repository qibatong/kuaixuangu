# -*- coding: utf-8 -*-
"""竞价一进二 路由 —— /api/yijiner(2026-09-28 新增)
===================================================================
独立端点, 与 /api/stocks(竞价选股) / /api/stocks_spot(盘中实时选股) 并列, 但语义独立:
  · 名单 = **昨日主板首板 → 今日竞价阶段评估二连板潜力**, 打分降序。
  · 门禁 = **严格 VIP/付费**(与「竞价异动」同强度; 免费试用 403, 连数据都拿不到)。
  · 只读 = 不落批次 / 不推送 / 不参与定格 / **不进 picker.pipeline 唯一选股链路**。
  · 不新开取数 = 涨停池走 fetcher.fetch_zt_pool, 行情走 fetcher.fetch_raw_by_codes。

评分公式逐函数照搬主人提供的独立网页版(含其 f4 缺陷), 见 services/yijiner.py 头注释。

调用: GET /api/yijiner            (空 date = 今天, 实时行情)
      GET /api/yijiner?date=YYYY-MM-DD  (历史回看; 🔴 口径近似, 响应带 approx=true)
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import kpl, yijiner
from .deps import jr, vip_or_paid_gate

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/yijiner")
def api_yijiner(request: Request, uid: int = Depends(vip_or_paid_gate("竞价一进二")),
                date: str = ""):
    """竞价一进二名单(仅 VIP/付费会员/管理员)。

    date 空 = 今天(实时行情, 与网页版逐位一致); 传 'YYYY-MM-DD' = **历史回看** ——
    该交易日的 9:25 快照 + 日K 重建(见 services/yijiner._hist_raw_rows)。
    ⚠️ 历史回看 **f26 上市日期 / f100 行业不可重建** ⇒ 次新过滤不生效、板块排名退化为单组,
      故响应标 `approx=true`(前端据此在标题旁提示), 不与"今天的精确口径"混为一谈。

    取数失败不抛异常, 统一返回 {ok:false, msg} + 空 list, 与既有只读端点风格一致
    (前端据此显示"稍后重试", 不会白屏)。
    """
    payload, err = yijiner.run(date=date or None)
    if err:
        log.warning("竞价一进二 取数失败 uid=%s date=%s err=%s", uid, date or "(今日)", err)
        return jr({"ok": False, "strategy": "yijiner", "msg": err, "list": [], "count": 0})
    if date:
        payload["approx"] = True          # f26/f100 不可重建, 前端需提示"近似口径"
        payload["reqDate"] = date
        # 概念: 历史行没有东财 f103 ⇒ 用概念库补(与竞价异动同源, 取前 2 个)
        try:
            kpl.apply_board_concept_db(payload.get("list") or [], log_tag="yijiner[hist]",
                                       field="concept", truncate=2, blank_if_missing=True,
                                       date=date)
        except Exception as e:            # noqa: BLE001 - 补概念失败不影响名单
            log.warning("竞价一进二 历史概念补齐失败 err=%s", e)
    log.info("竞价一进二 返回 uid=%s 数据日%s 请求日%s 近似=%s 入选%d 耗时%sms",
             uid, payload.get("dataDate"), date or "(今日)", bool(date),
             payload.get("count"), payload.get("elapsedMs"))
    return jr(payload)
