# -*- coding: utf-8 -*-
"""
AI 竞价预测报告路由 (2026-08-27 主人要求: aipick 加功能限制, 必须付费用户才能看)
=====================================================================================
原 /aipick/* 由 Nginx 静态 alias 匿名暴露(latest.html / predictions_*.html), 任何人可看。
改为: 关闭 Nginx 匿名静态, 报告内容统一经本接口鉴权后在 App 内查看。

  - GET /api/aipick/latest          最新预测报告(HTML)       需登录 + VIP/付费
  - GET /api/aipick/dates           已有预测报告日期列表(降序) 需登录 + VIP/付费
  - GET /api/aipick/detail/{date}   指定日期预测报告(HTML)    需登录 + VIP/付费

鉴权: require_vip_or_paid — 管理员 + VIP(member_level=2) + 付费会员(member_level=1)
可用; 免费试用(0)/未登录 一律 401/403, 彻底的付费门禁。
"""
import os
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..core import config, logger
from .deps import jr, require_vip_or_paid

log = logger.get_logger(__name__)

router = APIRouter()


def _report_dates():
    """已生成预测报告 `predictions_YYYY-MM-DD.html` 的日期列表(降序)。
    以输出目录下 predictions_*.html 为准, 兼容 latest.html。"""
    if not os.path.isdir(config.AIPICK_OUTPUT_DIR):
        return []
    dates = set()
    for f in os.listdir(config.AIPICK_OUTPUT_DIR):
        m = re.match(r"^predictions_(\d{4}-\d{2}-\d{2})\.html$", f)
        if m:
            dates.add(m.group(1))
    return sorted(dates, reverse=True)


def _read_report(path):
    """读取报告文件(UTF-8, 容错 BOM/GBK), 缺失返回 None"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return None


@router.get("/api/aipick/latest")
def api_aipick_latest(request: Request, uid: int = Depends(require_vip_or_paid)):
    """最新预测报告 HTML: 优先 latest.html, 缺失回退最近一个 predictions_.html"""
    latest = os.path.join(config.AIPICK_OUTPUT_DIR, "latest.html")
    html = _read_report(latest)
    if html is None:
        for d in _report_dates():
            html = _read_report(os.path.join(config.AIPICK_OUTPUT_DIR,
                                             f"predictions_{d}.html"))
            if html is not None:
                log.info("aipick latest.html 缺失, 回退 %s", d)
                break
    if html is None:
        return jr({"ok": False, "msg": "暂无预测报告, 交易日 9:30 前自动生成"}, 404)
    return HTMLResponse(html)


@router.get("/api/aipick/dates")
def api_aipick_dates(request: Request, uid: int = Depends(require_vip_or_paid)):
    """已有预测报告日期列表(降序), 供 App 内日期选择"""
    return jr({"ok": True, "dates": _report_dates()})


@router.get("/api/aipick/detail/{p_date}")
def api_aipick_detail(request: Request, p_date: str,
                      uid: int = Depends(require_vip_or_paid)):
    """指定日期的预测报告 HTML(仅放行 predictions_ 命名, 防路径穿越)"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p_date or ""):
        return jr({"ok": False, "msg": "日期格式有误"}, 400)
    path = os.path.join(config.AIPICK_OUTPUT_DIR, f"predictions_{p_date}.html")
    html = _read_report(path)
    if html is None:
        return jr({"ok": False, "msg": f"{p_date} 暂无预测报告"}, 404)
    return HTMLResponse(html)