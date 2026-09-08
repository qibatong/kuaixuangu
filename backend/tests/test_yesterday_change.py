# -*- coding: utf-8 -*-
"""昨日涨幅真实化单测 (2026-09-08 主人拍板)
背景: 评分的"昨日涨幅"因子(权重6%)分档语义是"昨日强势股延续"(3~9.5% 给 0.9 分),
但老实现喂的是**当日 f3**(现价涨幅) — 语义错配。本测试锁定修正后的行为:
  ① 日K 解析顺带取涨跌幅(零额外请求)
  ② 评分用真实昨日涨幅; 缺失走 default 分, 不再用 f3 冒充
"""
from app.services import fetcher, scorer


# ---------------- 日K 解析: 顺带取涨跌幅 ----------------
def test_kline_pair_returns_change():
    """东财日K 行: 日期,开,收,高,低,量,额,涨跌幅 → (pair, chg)"""
    klines = [
        "2026-09-04,10.0,10.2,10.3,9.9,1000,10200000,3.55",
        "2026-09-07,10.2,10.8,10.9,10.1,2000,21600000,5.88",   # 最近已收盘 T 日
    ]
    pair, chg = fetcher._kline_amount_pair(klines)
    assert pair == [2160.0, 1020.0]        # 万元
    assert chg == 5.88                     # T 日真实涨跌幅


def test_kline_pair_skips_today_row():
    """盘中含今天未收盘 K 线 → 跳过, 取最近已收盘交易日(其涨跌幅)"""
    today = fetcher._bj_date_str()
    klines = [
        "2026-09-07,10.2,10.8,10.9,10.1,2000,21600000,5.88",
        "%s,10.8,11.0,11.1,10.7,500,5500000,1.85" % today,     # 今天(未收盘)应跳过
    ]
    pair, chg = fetcher._kline_amount_pair(klines)
    assert pair[0] == 2160.0
    assert chg == 5.88                     # 不是今天的 1.85


def test_kline_pair_no_change_field():
    """K 线行缺涨跌幅字段(兜底源) → chg=None(缺失即缺失, 不捏造)"""
    klines = ["2026-09-07,10.2,10.8,10.9,10.1,2000,21600000"]
    pair, chg = fetcher._kline_amount_pair(klines)
    assert pair == [2160.0, None]
    assert chg is None


def test_kline_pair_invalid_returns_none_tuple():
    assert fetcher._kline_amount_pair([]) == (None, None)
    assert fetcher._kline_amount_pair(["bad,row"]) == (None, None)


# ---------------- 缓存读取: 零额外请求 ----------------
def test_fetch_yesterday_changes_reads_cache(monkeypatch):
    """fetch_yesterday_changes 只读 _yesterday_cache, 不发起任何网络请求"""
    called = []
    monkeypatch.setattr(fetcher, "_do_fetch_yesterday",
                        lambda *a, **k: called.append(1) or (0, 0))
    today = fetcher._bj_date_str()
    fetcher._yesterday_cache["600519"] = [today, [2160.0, 1020.0], 0.0, 5.88]
    fetcher._yesterday_cache["000001"] = [today, [900.0, 800.0], 0.0, None]   # 无涨幅
    out = fetcher.fetch_yesterday_changes(["600519", "000001", "300750"])
    assert out == {"600519": 5.88}         # 000001 缺失不出现, 300750 未缓存不出现
    assert called == [], "不得触发网络拉取"


# ---------------- 评分: 用真实昨日涨幅 ----------------
def _raw(f3=1.0, f2=10.0, f21=5e9, f615="3.0"):
    return {"f12": "600519", "f14": "测试", "f2": f2, "f3": f3, "f21": f21,
            "f615": f615, "f616": "5000000", "f5": 1000, "f18": 9.9, "f17": 10.0}


def test_score_uses_real_yesterday_change():
    """同一行行情: 昨日大涨 vs 昨日大跌 → 概率必须不同(证明因子真的吃的是昨日涨幅)"""
    p_up = scorer.compute_score(_raw(), yesterday_chg=8.0)["probability"]
    p_dn = scorer.compute_score(_raw(), yesterday_chg=-5.0)["probability"]
    assert p_up > p_dn, "昨日涨幅因子未生效: 昨涨8%%=%s 昨跌5%%=%s" % (p_up, p_dn)


def test_score_not_affected_by_f3_when_yesterday_given():
    """给了真实昨日涨幅后, 当日 f3 不得再影响"昨日涨幅"因子(老 bug: f3 冒充)"""
    a = scorer.compute_score(_raw(f3=9.9), yesterday_chg=2.0)["probability"]
    b = scorer.compute_score(_raw(f3=-9.9), yesterday_chg=2.0)["probability"]
    assert a == b, "昨日涨幅已给定, 当日 f3 不应再污染该因子(%s vs %s)" % (a, b)


def test_score_missing_yesterday_uses_default_not_zero():
    """缺失 → 走因子 default(0.15), 不是落进 [0,1) 桶(0.4), 更不是 0"""
    cfg = scorer.get_scoring_cfg()
    default_score = scorer._factor_default(cfg, "yesterday")
    p_missing = scorer.compute_score(_raw(), yesterday_chg=None)["probability"]
    p_zero = scorer.compute_score(_raw(), yesterday_chg=0.0)["probability"]
    assert default_score == 0.15
    assert p_missing != p_zero, "缺失不得等价于昨日涨幅 0(那是凭空给分)"


def test_score_all_stocks_passes_map():
    """score_all_stocks 必须把 yesterday_chg_map 透传给每只票"""
    raw = [_raw(), dict(_raw(), f12="000001")]
    scored = scorer.score_all_stocks(raw, yesterday_chg_map={"600519": 8.0})
    by_code = {x["code"]: x["probability"] for x in scored}
    # 600519 昨日+8% → 3~9.5% 桶 0.9 分; 000001 缺失 → default 0.15 分
    assert by_code["600519"] > by_code["000001"]


# ---------------- 契约层同步 ----------------
def test_contract_yesterday_not_from_f3():
    """契约层同样不得用 f3 冒充昨日涨幅"""
    from app.services.picker.contract import QuoteRow
    r = QuoteRow.from_eastmoney({"f12": "600519", "f3": 7.7})
    assert r.yesterday_change is None, "未传昨日涨幅时必须为 None"
    assert r.real_change == 7.7
    r2 = QuoteRow.from_eastmoney({"f12": "600519", "f3": 7.7}, yesterday_chg=2.1)
    assert r2.yesterday_change == 2.1


# ---------------- 成交额对形状契约(防接口 500) ----------------
def test_split_yday_unwraps_double_packed():
    """2026-09-08 测试机部署实证: 兜底源漏拆包返回 ((pair, chg), None) 双层,
    成交额对变成 tuple → scorer `bid_amt / pair[0]` 拿 list 做除法抛 TypeError,
    选股接口直接 500。_split_yday 必须剥掉多余一层, 保住成交额对形状。"""
    # 正常二元组
    assert fetcher._split_yday(([100.0, 200.0], 1.5)) == ([100.0, 200.0], 1.5)
    # 双层污染 → 剥壳后 pair 必须是数值 list, chg 正常带出
    pair, chg = fetcher._split_yday((([100.0, 200.0], 1.5), None))
    assert pair == [100.0, 200.0], "剥壳后成交额对应为 [T, T-1], 实际 %r" % (pair,)
    assert chg == 1.5
    # 纯成交额对(旧格式) / None
    assert fetcher._split_yday([100.0, 200.0]) == ([100.0, 200.0], None)
    assert fetcher._split_yday(None) == (None, None)


def test_fetch_yesterday_amounts_returns_plain_pair(monkeypatch):
    """端到端契约: fetch_yesterday_amounts 返回 {code: [T, T-1]万元} —
    scorer.score_all_stocks 直接 pair[0] 当成交额做除法, 形状错 = 选股接口 500。
    即便上游(兜底源)返回双层错误结构, 也必须还原成纯成交额对。"""
    monkeypatch.setattr(fetcher, "_yesterday_cache", {})
    # 模拟漏拆包的兜底源: 返回 ((pair, chg), None)
    monkeypatch.setattr(fetcher, "_fetch_yesterday_amount_one",
                        lambda code: (([100.0, 200.0], 1.5), None))
    monkeypatch.setattr(fetcher, "_do_fetch_yesterday",
                        lambda need, today: (len(need), 0))
    # 直接走真实的拆包+写缓存路径
    fetcher._yesterday_cache["600519"] = ["2000-01-01", None, 0.0, None]   # 强制判定为需拉取
    today = fetcher._bj_date_str()
    from app.services.fetcher import _split_yday
    v, vchg = _split_yday(fetcher._fetch_yesterday_amount_one("600519"))
    fetcher._yesterday_cache["600519"] = [today, v, 0.0, vchg]

    amts = fetcher.fetch_yesterday_amounts(["600519"])
    amt = amts["600519"]
    # 形状契约(防 500 的核心): 必须是纯 [T万元, T-1万元], 不得是 ((...), None) 双层
    assert isinstance(amt, list) and len(amt) == 2, \
        "成交额对必须是纯 [T, T-1] list, 实际 %r" % (amt,)
    assert all(isinstance(x, (int, float)) for x in amt), \
        "成交额对元素必须是数值(scorer 要拿 pair[0] 做除法), 实际 %r" % (amt,)
    # 且 scorer 侧能安全消费(不得抛 TypeError)
    chgs = fetcher.fetch_yesterday_changes(["600519"])
    assert chgs["600519"] == 1.5
