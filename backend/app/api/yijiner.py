# -*- coding: utf-8 -*-
"""竞价一进二 路由 —— /api/yijiner(2026-09-28 新增)
===================================================================
独立端点, 与 /api/stocks(竞价选股) / /api/stocks_spot(盘中实时选股) 并列, 但语义独立:
  · 名单 = **昨日主板首板 → 今日竞价阶段评估二连板潜力**, 打分降序。
  · 门禁 = **严格 VIP/付费**(与「竞价异动」同强度; 免费试用 403, 连数据都拿不到)。
  · 只读 = 不落批次 / 不推送 / 不参与定格 / **不进 picker.pipeline 唯一选股链路**。
  · 不新开取数 = 涨停池走 fetcher.fetch_zt_pool, 行情走 fetcher.fetch_raw_by_codes。

评分公式逐函数照搬主人提供的独立网页版(含其 f4 缺陷), 见 services/yijiner.py 头注释。

调用: GET /api/yijiner
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import yijiner
from .deps import jr, vip_or_paid_gate

log = logger.get_logger(__name__)

router = APIRouter()


@router.get("/api/yijiner")
def api_yijiner(request: Request, uid: int = Depends(vip_or_paid_gate("竞价一进二"))):
    """竞价一进二名单(仅 VIP/付费会员/管理员)。

    取数失败不抛异常, 统一返回 {ok:false, msg} + 空 list, 与既有只读端点风格一致
    (前端据此显示"稍后重试", 不会白屏)。
    """
    payload, err = yijiner.run()
    if err:
        log.warning("竞价一进二 取数失败 uid=%s err=%s", uid, err)
        return jr({"ok": False, "strategy": "yijiner", "msg": err, "list": [], "count": 0})
    log.info("竞价一进二 返回 uid=%s 数据日%s 入选%d 耗时%sms",
             uid, payload.get("dataDate"), payload.get("count"), payload.get("elapsedMs"))
    return jr(payload)
