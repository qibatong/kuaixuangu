# -*- coding: utf-8 -*-
"""P3 前端本地筛选快照接口回归(2026-09-12)

锁定四条性质, 每条都对应一个"发错就会让本地名单与后端名单分叉"的坑:

  1. **单位对齐**: 下发 floatMv(亿) / bidAmt(万元) 必须由**整数元**做与
     filter.apply_filters 完全相同的那一次除法得来 —— 元/亿 差 1e8 倍是这类接口
     的经典事故(本地筛选会把全市场当小盘股剔掉或全放行)。
  2. **行数闸门**: 物化表不完整(< MIN_ROWS, 如 9/11 熔断日只落 132 行) →
     enabled=false, 前端回退后端筛选; 绝不发半张表(否则名单凭空少一半)。
  3. **开关**: 关闭(默认) → enabled=false 且**不查库**; 天然灰度, 前后端可不同时上线。
  4. **门禁**: 非 VIP 拿不到全市场数据(403 由 FastAPI 抛出, 前端静默回退)。
"""
import json
from pathlib import Path

import pytest

from app.api import picker as api_picker
from app.db import database
from app.services import settings
from app.services.picker import filter as pfilter
from app.services.picker import precompute
from app.services.picker.contract import QuoteRow
from app.services.picker.score import ScoreResult, ScoredRow

_DATE = "2099-03-03"
_PREFIX = "PS"


def _code(i):
    return "%s%03d" % (_PREFIX, i)


def _q(code, **kw):
    base = dict(name="某股", bid_change=3.0, bid_amt=6.0e7, float_mv=55e8,
                prev_close=10.0, yesterday_change=1.0)
    base.update(kw)
    return QuoteRow(code=code, source="snapshot", **base)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    """假日期 + 放宽行数闸门 + 清接口缓存。

    行数闸门降到 1: 用例只造几只票(生产闸门 500 会判"预定格数据缺失")。
    闸门语义本身由 test_enabled_false_when_table_incomplete 单独覆盖。
    """
    monkeypatch.setattr(precompute, "MIN_ROWS", 1)
    api_picker._cache.clear()
    database.init_db()
    precompute.clear_date(_DATE)
    yield
    precompute.clear_date(_DATE)
    api_picker._cache.clear()
    settings.set(api_picker.SWITCH, 0)


def _seed(n=3, **kw):
    rows = {_code(i): _q(_code(i), **kw) for i in range(n)}
    st = precompute.precompute_all(_DATE, rows=rows, strengths={}, min_rows=1)
    assert st["ok"], st.get("error")
    return rows


# ==================== 1. 单位对齐 ====================
def test_snapshot_rows_units_match_filter_convention():
    """floatMv=亿 / bidAmt=万元, 且与 apply_filters 的除法同源(位级一致)"""
    _seed(1, float_mv=55e8, bid_amt=6.0e7, bid_change=3.0)
    got = precompute.read_snapshot_rows(_DATE, min_rows=1)
    assert len(got) == 1
    r = got[0]
    assert r["floatMv"] == 55e8 / 1e8          # 亿
    assert r["bidAmt"] == 6.0e7 / 1e4          # 万元
    assert r["bidChange"] == 3.0               # %(原样)
    assert r["isSt"] == 0 and r["isZt"] == 0
    assert r["prevClose"] == 10.0
    # 定格竞价价 = 昨收×(1+竞涨/100) —— 与契约 auction_price 同式(价格门槛用)
    assert r["auctionPrice"] == pytest.approx(10.0 * 1.03)


def test_snapshot_rows_ship_coarse_rank_matching_backend_key():
    """下发行的 coarseRank 必须等于后端同一函数的取值(2026-09-23 改键)。

    前端本地筛选用它做候选截断(与后端 filter.coarse_filter 同一把尺子), 而评分
    分档表与权重不下发前端 —— 所以这个标量一旦算错/算成别的口径, 本地名单就会
    与后端名单分叉, 且**没有任何别的用例能发现**。
    """
    from app.services import scorer
    from app.services.picker.score import coarse_rank_score

    rows = _seed(1)
    got = precompute.read_snapshot_rows(_DATE, min_rows=1)[0]
    assert got["coarseRank"] is not None
    assert got["coarseRank"] == pytest.approx(
        coarse_rank_score(rows[_code(0)], scorer.get_scoring_cfg()), abs=1e-4)


def test_snapshot_rows_keep_missing_as_none():
    """缺失字段保持 None(不得兜成 0) —— 0 是实测值, None 才是未知"""
    _seed(1, float_mv=None, bid_amt=None)
    got = precompute.read_snapshot_rows(_DATE, min_rows=1)
    assert got[0]["floatMv"] is None
    assert got[0]["bidAmt"] is None


# ==================== 2. 行数闸门 ====================
def test_enabled_false_when_table_incomplete():
    """物化表行数不足 → enabled=false(前端回退), 不发半张表"""
    _seed(2)
    # 闸门提到 500: 只有 2 行的表视为"缺失"
    assert precompute.read_snapshot_rows(_DATE, min_rows=500) == []


# ==================== 3. 开关 ====================
def test_switch_off_returns_disabled(client, vip_user):
    """开关关闭(默认) → enabled=false, 不查库不报错(前端静默回退原路径)"""
    token = vip_user[0]
    settings.set(api_picker.SWITCH, 0)
    _seed(3)
    d = client.get("/api/picker/snapshot?token=%s&date=%s" % (token, _DATE)).json()
    assert d["ok"] is True and d["enabled"] is False
    assert d["list"] == [] and d["count"] == 0


# ==================== 4. 门禁与正常下发 ====================
def test_free_user_gets_quota_not_403(client, second_user):
    """2026-09-21 配额制: 免费账号不再 403, 而是在配额内可正常取数。
    (原来免费账号拿不到全市场数据 → 现在改为每日 3 次配额, 用超才 429)"""
    token = second_user[0]
    settings.set(api_picker.SWITCH, 1)
    _seed(3)
    r = client.get("/api/picker/snapshot?token=%s&date=%s" % (token, _DATE))
    assert r.status_code == 200
    assert r.json().get("enabled") is True


def test_free_user_quota_exceeded_429(client, create_user_token):
    """免费账号超额 → 429 + code=quota_exceeded(前端据此弹开通引导)"""
    from app.core import config
    from app.db import database
    from app.services import quota as quota_svc
    u = create_user_token(member_level=0)
    token, uname = u["token"], u["username"]
    conn = database.get_conn()
    row = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
    conn.close()
    uid = int(row[0])
    settings.set(api_picker.SWITCH, 1)
    _seed(2)
    quota_svc.reset_user(uid, "picker", bonus=True)
    # 手动把额度耗尽
    for _ in range(int(config.QUOTA_PICKER_DAILY) + 1):
        quota_svc.consume(uid, "picker", dedup=False)
    r = client.get("/api/picker/snapshot?token=%s&date=%s" % (token, _DATE))
    assert r.status_code == 429
    assert r.json().get("detail", {}).get("code") == "quota_exceeded"


def test_enabled_returns_all_rows(client, vip_user):
    """开关开 + VIP + 物化表有数据 → 全市场行下发"""
    token = vip_user[0]
    settings.set(api_picker.SWITCH, 1)
    _seed(4)
    d = client.get("/api/picker/snapshot?token=%s&date=%s" % (token, _DATE)).json()
    assert d["enabled"] is True and d["count"] == 4
    codes = {r["code"] for r in d["list"]}
    assert codes == {_code(i) for i in range(4)}
    for r in d["list"]:
        assert "probability" in r and "confidence" in r


def test_enabled_false_when_materialized_missing(client, vip_user):
    """开关开但没有物化表(冷启动/批跑失败) → enabled=false, 前端回退(不报错)"""
    token = vip_user[0]
    settings.set(api_picker.SWITCH, 1)
    d = client.get("/api/picker/snapshot?token=%s&date=%s" % (token, _DATE)).json()
    assert d["ok"] is True and d["enabled"] is False


def test_switch_on_but_table_read_raises(client, vip_user, monkeypatch):
    """读物化表抛异常 → 也不能 500(前端拿不到快照就回退, 首页不受影响)"""
    token = vip_user[0]
    settings.set(api_picker.SWITCH, 1)

    def _boom(*a, **kw):
        raise RuntimeError("模拟物化表读取崩溃")

    monkeypatch.setattr(precompute, "read_snapshot_rows", _boom)
    r = client.get("/api/picker/snapshot?token=%s&date=%s" % (token, _DATE))
    assert r.status_code == 200
    assert r.json()["enabled"] is False


# ==================== 与前端 pickFromSnapshot 对拍(同一份夹具) ====================
_FIX = json.loads(
    (Path(__file__).parent / "fixtures" / "picker_parity.json").read_text(encoding="utf-8"))


def _fixture_rows():
    """夹具行 → ScoredRow(竞价额 万元→元, 市值 亿→元); 同时收集昨涨停集。"""
    zt = set()
    rows = []
    for r in _FIX["rows"]:
        if r.get("isZt"):
            zt.add(r["code"])
        row = QuoteRow(code=r["code"], name=r["name"], bid_change=r["bidChange"],
                       bid_amt=r["bidAmt"] * 1e4, float_mv=r["floatMv"] * 1e8,
                       prev_close=r["prevClose"])
        score = ScoreResult(probability=r["probability"],
                            confidence=r["confidence"], bid_turnover=None)
        rows.append(ScoredRow(row=row, score=score))
    return rows, zt


@pytest.mark.parametrize("case", _FIX["cases"],
                         ids=[c["name"] for c in _FIX["cases"]])
def test_parity_with_frontend_pick_from_snapshot(case):
    """后端 picker.filter 与前端 pickFromSnapshot 必须逐 case **同名单同顺序**。

    夹具由两侧共用(frontend/src/utils/pickFromSnapshot.test.js 读同一文件)。
    这是 P3 唯一的正确性防线 —— 本地秒筛的名单若与后端不同, 用户锁定/历史/推送
    会全线错位, 而且没人看得出来。
    """
    rows, zt = _fixture_rows()
    f = dict(case["filters"])
    f.setdefault("bidLt", 0)
    ctx = pfilter.FilterContext(markets=f["markets"], zt_codes=zt)
    codes = pfilter.coarse_filter([it.row for it in rows], f, ctx)
    idx = {it.row.code: it for it in rows}
    cand = [idx[c] for c in codes if c in idx]
    outcome = pfilter.apply_filters(cand, f, ctx)
    # 与 pipeline 输出同序(probability 降序, 同分 code 升序) —— 前端也做同样排序
    got = [it.row.code for it in sorted(
        outcome.kept, key=lambda x: (-x.score.probability, x.row.code))]
    assert got == case["expect"]
