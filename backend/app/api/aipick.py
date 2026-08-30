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
import json
import os
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import config, logger
from ..db import database
from ..services import fetcher, hot_rank, security, users
from .deps import jr, qs, require_vip_or_paid

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


def _read_json_report(date):
    """读取某日预测报告的 JSON 数据 `predictions_YYYY-MM-DD.json`, 缺失/损坏返回 None"""
    path = os.path.join(config.AIPICK_OUTPUT_DIR, f"predictions_{date}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _day_change_pct(closes, date):
    """由日K收盘算 date 当日涨跌幅(相对前收盘, %); 无法计算返回 None"""
    dates = sorted(closes)
    if not dates:
        return None
    # 定位 date 当天的最靠近交易日(穿越时用 ≥date 首根)
    idx = next((i for i, d in enumerate(dates) if d >= date), None)
    if idx is None or idx == 0:
        return None
    cur, prev = closes[dates[idx]], closes[dates[idx - 1]]
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 2)


def _attach_day_change(data, date):
    """给报告 JSON 的每行 top（以及全量候选 all，若存在）附上 date 当日涨跌幅 day_change。
    用于回看历史报告时展示"当日涨幅"。

    性能: 全量候选 all 可能数千只, 用**单个连接**批量读本地 stock_kline, 绝不发网络,
    避免为逐只看历史 K 线把接口拖死。缺缓存的置 None, 前端显示 '--'。"""
    tops = data.get("top")
    groups = []
    if isinstance(tops, list):
        groups.append(tops)
    if isinstance(data.get("all"), list):
        groups.append(data["all"])
    if not groups:
        return data
    cache = {}
    try:
        conn = database.get_conn()
    except Exception:
        return data
    try:
        for rows in groups:
            for r in rows:
                code = r.get("code")
                if not code:
                    r["day_change"] = None
                    continue
                closes = cache.get(code)
                if closes is None:
                    closes = {}
                    try:
                        row = conn.execute(
                            "SELECT day_data FROM stock_kline WHERE code=?", (code,)).fetchone()
                        if row and row[0]:
                            closes = _closes_from_json(row[0])
                    except Exception:
                        pass
                    cache[code] = closes
                r["day_change"] = _day_change_pct(closes, date)
    finally:
        conn.close()
    return data


def _closes_from_json(raw):
    """从 stock_kline.day_data JSON 提取按交易日升序的 {date: close}"""
    out = {}
    try:
        data = json.loads(raw)
        times, closes = data.get("time", []), data.get("close", [])
        for i, d in enumerate(times):
            try:
                out[str(d)] = float(closes[i])
            except Exception:
                continue
    except Exception:
        pass
    return out


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


@router.get("/api/aipick/realtime")
def api_aipick_realtime(request: Request, codes: str = "",
                        uid: int = Depends(require_vip_or_paid)):
    """实时行情(实时涨幅): 报告日期是历史快照(9:25竞价), 实时涨幅需动态取东财行情。
    codes 形如 "600721,000017,..."(逗号/空格分隔), 一次最多 200 只。"""
    codes = [c for c in re.split(r"[,，\s]+", codes or "") if re.fullmatch(r"\d{6}", c)]
    if not codes:
        return jr({"ok": False, "msg": "缺少股票代码"})
    codes = codes[:200]
    secids = [("1." + c if c.startswith("6") else "0." + c) for c in codes]
    quotes = hot_rank._fetch_em_quotes(secids)
    return jr({"ok": True, "quotes": quotes})


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


@router.get("/api/aipick/data")
def api_aipick_data(request: Request, uid: int = Depends(require_vip_or_paid)):
    """最新预测报告数据(JSON)。App 内直接原生渲染表格, 不再嵌套 iframe 老页面。"""
    dates = _report_dates()
    if not dates:
        return jr({"ok": False, "msg": "暂无预测报告, 交易日 9:30 前自动生成"}, 404)
    data = _read_json_report(dates[0])
    if not data:
        return jr({"ok": False, "msg": "最新预测数据读取失败"}, 500)
    return jr({"ok": True, "data": data})


@router.get("/api/aipick/data/{p_date}")
def api_aipick_data_date(request: Request, p_date: str,
                         uid: int = Depends(require_vip_or_paid)):
    """指定日期预测报告数据(JSON)。若为历史日期, 每行补充当日涨跌幅 day_change(当日涨幅)。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p_date or ""):
        return jr({"ok": False, "msg": "日期格式有误"}, 400)
    data = _read_json_report(p_date)
    if not data:
        return jr({"ok": False, "msg": f"{p_date} 暂无预测报告"}, 404)
    _attach_day_change(data, p_date)
    return jr({"ok": True, "data": data})


@router.get("/api/aipick/auth-check")
def api_aipick_auth_check(request: Request):
    """Nginx auth_request 子请求校验: 静态 /aipick/* 放行但仅限 VIP/付费/管理员。

    用途(2026-08-30 主人要求): 之前 /aipick/ 匿名静态完全关闭(404),
    现放开 latest.html / predictions_*.html 静态访问, 但每个请求先经 Nginx
    auth_request 转发到本接口校验 token(VIP/付费/管理员才放行, 免费试用 403)。
    - 鉴权 token 来源(按优先级):
      1. Nginx 透传头 X-Original-Authorization (Bearer xxx, auth_request 子请求场景)
      2. 原始请求 URI 的 query token (Nginx X-Original-URI 透传)
      3. 本请求自身的 query token 或 Authorization 头 (直连 /api 调试场景)
    - 返回 200 = 放行(给 Nginx auth_request 用, 响应体忽略)
    - 返回 401/403 = Nginx 拒发静态文件(转 401/403 给客户端)
    """
    # 1) Nginx 子请求透传的 Authorization 头
    token = ""
    xauth = request.headers.get("X-Original-Authorization") or ""
    if xauth.startswith("Bearer "):
        token = xauth[7:].strip()
    # 2) Nginx 透传的原始 URI 里的 ?token=
    if not token:
        xuri = request.headers.get("X-Original-URI") or ""
        m = re.search(r"[?&]token=([0-9a-fA-F]{20,64})", xuri)
        if m:
            token = m.group(1)
    # 3) 直连场景: 本请求自身的 query token / Authorization
    if not token:
        token = (qs(request).get("token") or [""])[0]
    if not token:
        auth = request.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    status, uid = security._token_status(token)
    if status != "ok":
        return JSONResponse({"ok": False, "code": "expired", "msg": "未登录或登录已过期"},
                            status_code=401)
    u = users.find_user_by_id(uid)
    if not u:
        return JSONResponse({"ok": False, "code": "no_user", "msg": "用户不存在"}, status_code=401)
    if u.get("is_admin") or int(u.get("member_level") or 0) >= 1:
        return JSONResponse({"ok": True, "uid": uid}, status_code=200)
    return JSONResponse({"ok": False, "code": "vip_required",
                         "msg": "AI 预测仅限 VIP/付费会员，请升级后使用"}, status_code=403)