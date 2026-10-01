# -*- coding: utf-8 -*-
"""超智研判（聚合页）路由 —— 原「AI预测」升级版。

    GET /api/chaozhi/overview    一屏聚合：三个得分 + 情绪/资金 10 日序列 + 双模型个股 + 风险档位

🔴 两条纪律（详见 `services/chaozhi.py` 模块头）：
   ① **只读 + 零新增上游出网**（复用已缓存函数 + 本地库/本地 json）；
   ② **不加 `quota_guard`** —— 这里只读 aipick 输出目录的 json 文件（不触发推理），
      若照抄 `/api/aipick/data` 的 `Depends(quota_guard("aipick"))`，聚合页每次进入都会
      消耗用户当天的 AI 预测配额（免费用户仅 1 次/日），属于必须避开的坑。
"""
from fastapi import APIRouter, Depends, Request

from ..core import logger
from ..services import chaozhi
from .deps import get_uid, jr

router = APIRouter()

log = logger.get_logger("chaozhi")


@router.get("/api/chaozhi/overview")
def api_chaozhi_overview(request: Request, uid: int = Depends(get_uid)):
    """超智研判聚合数据（跨进程 60s 缓存 + 单飞）。

    整页不因单块失败而失败：不可用的块在 `meta.notes` 里如实列出，前端按 notes 展示降级原因。
    """
    try:
        data = chaozhi.build_overview() or {}
    except Exception as e:                                     # noqa: BLE001
        log.warning("chaozhi overview 失败 uid=%s err=%s", uid, str(e)[:160])
        return jr({"ok": False, "msg": "聚合数据暂不可用，请稍后重试"})
    return jr({"ok": True, **data})
