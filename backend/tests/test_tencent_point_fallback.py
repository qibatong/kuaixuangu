# -*- coding: utf-8 -*-
"""2026-09-08 方案 A+(主人拍板): 东财 ulist 点查失败 → 先切腾讯 qt.gtimg.cn 按 code 点查
(fetch_tencent_by_codes), 腾讯也失败才降级 9:25 快照行直出 — 不再只降级「无实时行情
的快照行」(9/8 早生产实锤: 东财断连 30+ 分钟 → 快照行直出名单 real_change 全 0 +
price=0 让 priceGt 失效/confidence 偏高让双低剔除失效 → 同参数名单虚胖 35→70 只)。

计数口径(2026-09-09): 选股主链路切到 picker.pipeline 后, 补丁源(新链路)
与 raw 准备(老路径)各点查一次 → 计数断言为"至少一次"; 真正要守住的是
**ensure_cache 实时全市场 0 次**(名单波动与加载慢的根因)。

核心不变量:
1. 东财点查失败 → 腾讯点查被调用, 名单有真实实时涨幅(realChange≠0), 不拉实时全市场
2. 东财+腾讯都失败 → 快照行直出兜底(保名单非空), 仍不拉实时全市场
3. fetch_tencent_by_codes 映射正确(与 fetch_raw_by_codes 同构, 可直接喂 scorer)
"""
import pytest

from app.services import auction_snapshot, fetcher, scorer

# 与 test_stocks.py 同款: conftest 的 MOCK_RAW(全通过默认筛选的行情行)
from conftest import MOCK_RAW

# conftest 的 session fixture 会把 fetch_tencent_by_codes 桩掉(防测试打真实网络),
# 但本文件底部的**映射正确性单测**必须跑真实实现。模块导入早于 session fixture →
# 此刻拿到的是未被桩的原始函数, 用 autouse fixture 还给这三个用例。
_REAL_TX = fetcher.fetch_tencent_by_codes


@pytest.fixture(autouse=True)
def _restore_real_tencent(monkeypatch):
    """映射单测需要真实实现; 用例内若自行 monkeypatch 则以其为准"""
    monkeypatch.setattr(fetcher, "fetch_tencent_by_codes", _REAL_TX)


def _snap_rows():
    """构造 load_snapshot_full 返回值 — 600001 为合格候选"""
    return {"600001": {"name": "测试甲", "bid_change": 3.5, "bid_amt": 5000.0,
                       "free_mv": 500.0 * 1e8}}


def _url(action):
    return ("/api/stocks?action=%s&strategy=auction&markets=hs,cyb,kcb&bidGt=7&probLt=65"
            "&confLt=65&floatMvGt=1000&priceGt=300&bidAmtFloor=3000" % action)


def _mock_8am(monkeypatch):
    """凌晨 8:00(9:15 前, 当日无快照/无竞价) — 覆盖 session 默认 9:25"""
    monkeypatch.setattr(scorer, "bj_now", lambda: (8, 0, True))
    monkeypatch.setattr(scorer, "_bj_hm", lambda: 8 * 60)
    monkeypatch.setattr(scorer, "in_auction_window", lambda: False)


def _mock_snap_pool_base(monkeypatch, calls):
    """桩掉快照池数据源与 ensure_cache(计数 ensure); 返回 calls dict"""
    monkeypatch.setattr(auction_snapshot, "load_snapshot_full", lambda *a, **kw: _snap_rows())
    monkeypatch.setattr(auction_snapshot, "load_snapshot", lambda: {})
    monkeypatch.setattr(auction_snapshot, "load_day_bid_amt",
                        lambda: {"600001": 5000.0})     # 万元, ≥bidAmtFloor
    monkeypatch.setattr(auction_snapshot, "load_day_bid_change",
                        lambda: {"600001": 3.5})

    def fake_ensure(action, fs, before930):
        if action == "lock" and not before930:
            return None, "9:30 后禁止重新选股"
        calls["ensure"] += 1
        return [], None
    monkeypatch.setattr(fetcher, "ensure_cache", fake_ensure)
    return calls


def _get(client, token, action):
    return client.get(_url(action), headers={"Authorization": "Bearer " + token})


def _tencent_like_row():
    """腾讯点查返回的行(与 MOCK_RAW[0] 同构): f3=3.20 非 0 → 证明实时行情进入评分/展示。
    字段语义同 _tencent_diff: f2现价/f3涨跌/f4昨收/f5量/f17今开/f21流通市值(元)"""
    row = dict(MOCK_RAW[0])
    row["f2"] = 18.50
    row["f3"] = 3.20
    row["f21"] = 500.0 * 1e8        # 500 亿流通(≤ floatMvGt=1000 亿)
    return row


# ========== 用例 1: 东财点查失败 → 腾讯点查兜底成功 ==========
def _tx_fields(code, name, price, pre_close, open_p, chg, vol_hand, amt_wan,
               turnover, mv_yi):
    """伪造腾讯 ~ 分隔 88 字段列表(0-based, 长度 >47)"""
    f = [""] * 60
    f[1] = name
    f[3] = str(price)
    f[4] = str(pre_close)
    f[5] = str(open_p)
    f[32] = str(chg)
    f[36] = str(vol_hand)
    f[37] = str(amt_wan)
    f[38] = str(turnover)
    f[44] = str(mv_yi)
    return f


def test_fetch_tencent_by_codes_maps_diff(monkeypatch):
    """腾讯点查返回 → 东财 diff 同构(可直接喂 scorer)"""
    monkeypatch.setattr(fetcher, "_check_circuit", lambda name: False)
    fields = _tx_fields("600001", "测试甲", 9.53, 8.66, 9.03, 3.50,
                        607362, 56917, 11.51, 50.30)
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch",
                        lambda batch: {"600001": list(fields)})
    out = fetcher.fetch_tencent_by_codes(["600001"])
    assert len(out) == 1
    d = out[0]
    assert d["f12"] == "600001" and d["f14"] == "测试甲"
    assert d["f2"] == 9.53 and d["f3"] == 3.50
    assert d["f4"] == 8.66 and d["f17"] == 9.03
    assert d["f5"] == 607362 and d["f8"] == 11.51
    assert abs(d["f21"] - 50.30 * 1e8) < 1e-3, "流通市值: 亿 → 元"
    assert d["f615"] == 3.50 and d["f616"] == 56917 * 1e4 and d["f617"] == 607362 * 100
    assert d["f630"] == 0


def test_fetch_tencent_by_codes_empty_raises(monkeypatch):
    """腾讯点查返回空 → 抛 RuntimeError(调用方落回快照行直出)"""
    monkeypatch.setattr(fetcher, "_check_circuit", lambda name: False)
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch", lambda batch: {})
    with pytest.raises(RuntimeError):
        fetcher.fetch_tencent_by_codes(["600001"])


def test_fetch_tencent_by_codes_bad_fields_skipped(monkeypatch):
    """单行字段缺关键数值(如停牌无价) → 跳过该行, 不整体失败"""
    monkeypatch.setattr(fetcher, "_check_circuit", lambda name: False)
    bad = [""] * 60                     # 全空 → float 解析失败
    good_fields = _tx_fields("600001", "测试甲", 9.53, 8.66, 9.03, 3.50,
                             607362, 56917, 11.51, 50.30)
    monkeypatch.setattr(fetcher, "_fetch_tencent_batch",
                        lambda batch: {"600002": list(bad), "600001": list(good_fields)})
    out = fetcher.fetch_tencent_by_codes(["600001", "600002"])
    assert [d["f12"] for d in out] == ["600001"], "坏行应跳过, 好行保留"
