# -*- coding: utf-8 -*-
"""定格闸门 v4.11.29 (2026-09-18): 只认**当日** 9:25 定格

背景(主人现象, 2026-09-18): 测试环境下午刷新, 页面显示的 5 只票(华瓷/西陇/黑猫/芒果/澳弘)
竞涨幅**逐位 = 9/17 的值**(黑猫 3.35, 今日实为 1.00), 流通与评分也和当日 09:15 锁定的
批次 #1674 完全一致 —— 即"09:15 用昨日定格算出的批次"被 refresh 直读 + 前端回显了整天。
竞涨幅占评分权重 34% ⇒ 名单与评分双双失真, 必须从"批次复用"这一层堵掉。

判据是**数据驱动**的(不是硬编码时刻):
    批次 ts >= 当日 9_25 快照的落库 ts(snapshot_bid.ts, 该时点全部行同一值)
理由: 落库时刻每天在漂(实测 09:25:23~09:25:32), 而**系统批次(#9_25)由落库事件本身
触发** —— 9/18 实测系统批次 #1676 只比落库晚 **3 秒**(snapshot ts=1789694726,
#1676 ts=1789694729)。若用固定的 "09:25:36" 判, 这份**合法**名单会被误判成"定格前"
→ refresh 掉到跨日回退 → 显示昨日名单(本文件 test_system_batch_lands_just_after_freeze
就是这条回归防线)。

本文件钉死:
  ① `_freeze_landing_ts` / `_is_freeze_ready_batch` 纯函数语义;
  ② `find_today_reusable_batch` / `find_today_system_batch` 的批次过滤;
  ③ `list_batches` 透出 `freeze_ready`(前端首屏回显的判据来源);
  ④ `stocks._freeze_fields` 定格来源日期(顶栏标注);
  ⑤ 前端同口径(用后端的 freeze_ready, 不自己猜时刻)。
"""
import calendar
import json

import pytest

from app.api import stocks as stocks_api
from app.db import database
from app.services import auction_snapshot, history
from app.services.picker import mode as pm

# ---- 固定"当前时刻": 2026-09-18(周五) 北京 09:31 / 10:00(均 ≥ 定格放行点) ----
TS_0931 = calendar.timegm((2026, 9, 18, 1, 31, 0, 0, 0, 0))
TS_1000 = calendar.timegm((2026, 9, 18, 2, 0, 0, 0, 0, 0))
BDATE = "2026-09-18"
UID_BAND = (992300, 992399)
# 单独用于"快照落库 + 批次"组合验证的**合成日期**(避开其它用例的日期, 防互相污染)
SDATE = "2026-08-03"
LAND = 1789694726            # 合成"定格落库时刻"
TS_PRE = LAND - 600          # 落库前 10 分钟写入的批次(竞价字段=昨日)
TS_POST = LAND + 3           # 落库后 3 秒(系统批次实测形态)


@pytest.fixture(autouse=True)
def _clean_shared_batches():
    """清理跨用例共享数据(系统批次 user_id=0 跨用例可见) + 本文件的 uid/合成日期残留"""
    conn = database.get_conn()
    conn.execute("DELETE FROM batches WHERE user_id=0 AND auto_applied=1 AND batch_date=?",
                 (BDATE,))
    conn.execute("DELETE FROM batches WHERE user_id BETWEEN ? AND ?", UID_BAND)
    conn.execute("DELETE FROM snapshot_bid WHERE date=?", (SDATE,))
    conn.execute("DELETE FROM batches WHERE batch_date=?", (SDATE,))
    conn.commit()
    conn.close()
    yield


def _f(**over):
    """与 scorer.validate_filters 输出同构的筛选参数(指纹比对用)"""
    f = {"stSuspend": True, "limitUp": True, "markets": ["hs", "cyb", "kcb"],
         "bidGt": 7, "probLt": 65, "confLt": 65, "floatMvFloor": 30, "floatMvGt": 100,
         "priceGt": 30, "bidAmtFloor": 3000, "chgFloor": 0, "chgGt": 9.5,
         "volRatioFloor": 1, "turnoverFloor": 1, "turnoverGt": 0, "spotExcludeZT": False}
    f.update(over)
    return f


def _seed_batch(uid, f, action="lock", auto_applied=0, bdate=BDATE, btime="09:26:00",
                ts=TS_0931, n=2):
    """种一条批次行(batch_date/ts 可控 —— 本文件的核心变量)"""
    filters_json = json.dumps({k: v for k, v in f.items() if k != "markets"},
                              ensure_ascii=False)
    markets = ",".join(f["markets"])
    conn = database.get_conn()
    cur = conn.execute(
        "INSERT INTO batches (batch_date, batch_time, ts, action, markets, filters, "
        "stock_count, user_id, auto_applied) VALUES (?,?,?,?,?,?,?,?,?)",
        (bdate, btime, ts, action, markets, filters_json, n, uid,
         1 if auto_applied else 0))
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return bid


def _seed_freeze(bdate, ts=LAND, codes=("600001", "600002")):
    """种当日 9_25 快照行(同一点的所有行 ts 相同 —— 与生产实际一致)"""
    conn = database.get_conn()
    for c in codes:
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_bid (date, time_point, code, bid_change, "
            "bid_amt, ts, name) VALUES (?,?,?,?,?,?,?)",
            (bdate, "9_25", c, 1.23, 4567.0, ts, "测试股"))
    conn.commit()
    conn.close()


def _drop_freeze(bdate):
    conn = database.get_conn()
    conn.execute("DELETE FROM snapshot_bid WHERE date=?", (bdate,))
    conn.commit()
    conn.close()


# ============================================================
# ① 判据纯函数
# ============================================================
def test_freeze_ready_predicate_is_ts_based():
    """判据 = 批次 ts >= 定格落库 ts; 落库前一律 False, 等号算通过(同秒写入)"""
    def row(ts):
        return {"ts": ts}

    assert history._is_freeze_ready_batch(row(TS_PRE), LAND) is False     # 落库前
    assert history._is_freeze_ready_batch(row(LAND - 1), LAND) is False
    assert history._is_freeze_ready_batch(row(LAND), LAND) is True        # 同秒
    assert history._is_freeze_ready_batch(row(TS_POST), LAND) is True     # 系统批次形态
    assert history._is_freeze_ready_batch(row(LAND + 3600), LAND) is True


def test_freeze_ready_predicate_no_landing():
    """当日无 9_25 行(landing=None) → 一律判定为"定格前"(退回复用会让昨日名单上屏)"""
    assert history._is_freeze_ready_batch({"ts": TS_POST}, None) is False
    assert history._is_freeze_ready_batch({"ts": TS_POST}, 0) is False


def test_freeze_ready_predicate_dirty_ts():
    """ts 缺失/为 0 的脏数据 → 保守视为定格前(不复用)"""
    assert history._is_freeze_ready_batch({"ts": None}, LAND) is False
    assert history._is_freeze_ready_batch({}, LAND) is False


def test_freeze_landing_ts_reads_snapshot():
    """_freeze_landing_ts 读当日 9_25 的落库 ts; 无该行 → None"""
    _seed_freeze(SDATE, ts=LAND)
    assert history._freeze_landing_ts(SDATE) == LAND
    assert history._freeze_landing_ts("1990-01-01") is None
    _drop_freeze(SDATE)
    assert history._freeze_landing_ts(SDATE) is None


def test_gate_open_point_documented_value():
    """闸门放行点 09:26:31 / 拦截上界 09:26:30(v4.11.45 与定格枪对齐; 原 09:25:51/09:25:50)

    注意它**不是**批次复用判据 —— 复用判据是数据时间戳(见文件头注释)。
    """
    assert pm.T_PICK_OPEN == 9 * 3600 + 26 * 60 + 31
    assert pm.T_PICK_BLOCK_FROM == 9 * 3600 + 15 * 60
    assert pm.T_PICK_BLOCK_TO == 9 * 3600 + 26 * 60 + 30


# ============================================================
# ② 定格前批次不复用(主人现象的直接回归防线)
# ============================================================
def test_pre_freeze_lock_batch_not_reused():
    """🔴 定格落库前写入的 lock 批次(= #1674 形态)不得被 refresh 直读复用"""
    _seed_freeze(SDATE)
    uid = 992301
    f = _f()
    _seed_batch(uid, f, action="lock", bdate=SDATE, btime="09:15:05", ts=TS_PRE)
    g = calendar.timegm((2026, 8, 3, 2, 0, 0, 0, 0, 0))       # 北京 2026-08-03 10:00
    # 该日只有这一条(定格前) → 当日版必须 miss, 不得把它当权威名单
    assert history.find_today_reusable_batch(uid, f, now_ts=g) == (None, None)


def test_pre_freeze_filter_batch_not_reused():
    """同理: 竞价段(09:22)落库的 filter 批次也不得复用(用纯函数判据逐条核对)"""
    land = LAND
    pre = {"action": "filter", "ts": TS_PRE, "batch_time": "09:22:38"}
    post = {"action": "filter", "ts": TS_POST, "batch_time": "09:25:29"}
    assert history._is_freeze_ready_batch(pre, land) is False
    assert history._is_freeze_ready_batch(post, land) is True


def test_freeze_ready_lock_batch_reused():
    """定格落库后的 lock 批次照旧可用 —— 不能把闸门改坏成"什么都不复用"
    (那会让 9:30 后每次刷新都全市场重算, 多用户排队长尾 57s 的老问题复发)"""
    uid = 992303
    f = _f()
    _seed_freeze(SDATE)
    land = history._freeze_landing_ts(SDATE)
    r = {"action": "lock", "ts": TS_POST, "batch_time": "09:25:40", "stock_count": 2}
    assert history._is_freeze_ready_batch(r, land) is True
    # 真实选批: 用同一天的批次 + 注入 now_ts 让"今天"对齐 → 命中
    bid = _seed_batch(uid, f, action="lock", bdate=SDATE, btime="09:25:40", ts=TS_POST)
    g = calendar.timegm((2026, 8, 3, 2, 0, 0, 0, 0, 0))       # 北京 2026-08-03 10:00
    assert history.find_today_reusable_batch(uid, f, now_ts=g) == (bid, "lock")


def test_system_batch_lands_just_after_freeze():
    """🔴 系统批次判据回归防线: 落库后 **3 秒**写入的系统批次必须可用

    9/18 实测: snapshot 9_25 ts=1789694726, 系统批次 #1676 ts=1789694729。
    若判据用固定的 "09:25:36"(#1676 是 09:25:29) → 这份合法名单被误杀 →
    refresh 掉到跨日回退 → 显示昨日名单。这里用 ts 判据把它钉住。
    """
    _seed_freeze(SDATE)
    bid = _seed_batch(0, _f(), action="lock", auto_applied=1, bdate=SDATE,
                      btime="09:25:29", ts=TS_POST)
    g = calendar.timegm((2026, 8, 3, 2, 0, 0, 0, 0, 0))
    assert history.find_today_system_batch(now_ts=g) == (bid, "auto")


def test_system_batch_before_freeze_rejected():
    """落库**前**写入的系统批次(异常形态)必须拒绝"""
    _seed_freeze(SDATE)
    _seed_batch(0, _f(), action="lock", auto_applied=1, bdate=SDATE,
                btime="09:15:05", ts=TS_PRE)
    g = calendar.timegm((2026, 8, 3, 2, 0, 0, 0, 0, 0))
    assert history.find_today_system_batch(now_ts=g) == (None, None)


def test_system_batch_none_when_no_freeze():
    """当日无 9:25 定格(未落库) → 无可用系统批次(否则等于复用昨日口径名单)"""
    _drop_freeze(SDATE)
    _seed_batch(0, _f(), action="lock", auto_applied=1, bdate=SDATE,
                btime="09:26:00", ts=TS_POST)
    g = calendar.timegm((2026, 8, 3, 2, 0, 0, 0, 0, 0))
    assert history.find_today_system_batch(now_ts=g) == (None, None)


# ============================================================
# ③ 批次列表透出 freeze_ready(前端首屏回显的判据)
# ============================================================
def test_list_batches_exposes_freeze_ready():
    """list_batches 每行带 freeze_ready —— 前端据此排除定格前批次

    前端拿不到 snapshot_bid 的落库时刻, 判据**必须**由后端给出(否则前端只能猜时刻,
    而猜必然在"系统批次(落库后 3 秒)"上出错)。
    """
    _seed_freeze(SDATE)
    uid = 992305
    pre = _seed_batch(uid, _f(), action="lock", bdate=SDATE, btime="09:15:05", ts=TS_PRE)
    post = _seed_batch(uid, _f(), action="lock", bdate=SDATE, btime="09:25:40", ts=TS_POST)
    got = {b["id"]: b.get("freeze_ready") for b in history.list_batches(uid)}
    assert got.get(pre) is False, "定格前批次 freeze_ready 必须为 False"
    assert got.get(post) is True, "定格后批次 freeze_ready 必须为 True"


def test_list_batches_freeze_ready_false_without_freeze():
    """当日无 9:25 定格 → 当日批次的 freeze_ready 全为 False"""
    _drop_freeze(SDATE)
    uid = 992306
    b = _seed_batch(uid, _f(), action="lock", bdate=SDATE, btime="09:26:00", ts=TS_POST)
    got = {x["id"]: x.get("freeze_ready") for x in history.list_batches(uid)}
    assert got.get(b) is False


# ============================================================
# ④ 定格来源日期透出(前端顶栏标注)
# ============================================================
def _patch_freeze(monkeypatch, ret=None, raises=False):
    if raises:
        def _boom(date=None):
            raise RuntimeError("db down")
        monkeypatch.setattr(auction_snapshot, "freeze_source_date", _boom)
    else:
        monkeypatch.setattr(auction_snapshot, "freeze_source_date",
                            lambda date=None: ret)


def test_freeze_fields_today(monkeypatch):
    """当日定格可用 → freezeIsToday=True(前端不出标注条)"""
    today = pm.bj_date()
    _patch_freeze(monkeypatch, ret=today)
    assert stocks_api._freeze_fields() == {"freezeDate": today, "freezeIsToday": True}


def test_freeze_fields_previous_day(monkeypatch):
    """盘前/非交易日用上一交易日定格 → freezeIsToday=False + 带来源日期(前端出标注条)"""
    today = pm.bj_date()
    prev = "2026-09-17"
    assert prev != today
    _patch_freeze(monkeypatch, ret=prev)
    assert stocks_api._freeze_fields() == {"freezeDate": prev, "freezeIsToday": False}


def test_freeze_fields_db_error_is_harmless(monkeypatch):
    """查库异常 → 返回 {} (宁可少标也不误标成"当日"), 且不得抛错影响选股"""
    _patch_freeze(monkeypatch, raises=True)
    assert stocks_api._freeze_fields() == {}


def test_freeze_source_date_real_query():
    """真实查库不抛错, 且返回值是 10 位日期字符串"""
    d = auction_snapshot.freeze_source_date("2026-09-18")
    assert isinstance(d, str) and len(d) == 10


# ============================================================
# ⑤ 前端同口径
# ============================================================
def _fe(rel):
    """读前端源码(用于前后端同口径对拍)。

    若本机没有前端源码、或只有**旧副本**(如服务器上残留的 08-25 版 frontend/src),
    则跳过 —— 与旧副本对拍会给出假红/假绿, 没有意义。判定方式与本文件断言无关:
    看 `utils/time.js` 是否含 v4.11.29 闸门文案(该文件是闸门口径的单一事实源)。
    """
    import io
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", "frontend"))
    p = os.path.join(root, "src", rel)
    tj = os.path.join(root, "src", "utils", "time.js")
    if not os.path.exists(p):
        pytest.skip("前端源码不在本仓库布局内")
    if not os.path.exists(tj) or "竞价进行中" not in io.open(tj, encoding="utf-8").read():
        pytest.skip("本机前端源码非最新副本(缺 v4.11.29 闸门文案), 对拍无意义")
    return io.open(p, encoding="utf-8").read()


def test_frontend_uses_backend_freeze_ready():
    """前端必须用后端的 freeze_ready 判据, 不得自己硬编码时刻

    后端排除了还不够: 前端首屏是**先查批次回显**(loadLockedBatchFromServer),
    今天主人看到的 #1674 就是被这一路回显的。两处必须同口径。
    """
    src = _fe("stores/stocks.js")
    assert "freezeReady" in src, "前端未做定格前批次过滤"
    assert "x.freezeReady === true" in src, "前端未采用后端 freeze_ready 判据"
    # 反向防线: 别再退回"用固定时刻猜"的写法(会在系统批次上误杀)
    assert ">= '09:25:36'" not in src, "前端又用固定时刻猜定格, 会在系统批次上误杀"


def test_frontend_has_freeze_notice():
    """顶栏必须有定格来源标注条(盘前/非交易日用上一交易日定格, 不能静默)"""
    src = _fe("views/StockView.vue")
    assert "freeze-notice" in src
    assert "freezeIsToday" in src
