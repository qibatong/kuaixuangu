# -*- coding: utf-8 -*-
"""北交所纳入(2026-09-29 主人拍板) —— 市场白名单 / 过滤 / 默认值 / 采集与概念层。

背景: 2026-09-21 曾拍板「系统不需要北交所数据」, 在四处加了 `scorer.is_bse()` 排除
      (快照 ×2 / 概念 ×1 / 题材成分股 ×4)。2026-09-29 主人改为「**北交所纳入**」⇒
      前三处撤销, **题材/板块成分股视图仍保持排除**(本轮刻意不动, 有测试盯着)。
"""
import pytest

from app.services import scorer
from app.services import filter_defaults as fd
from app.services.picker import filter as pf

BJ_CODES = ("920267", "830799", "430047", "871981", "889999")


# ---------------- 东财 fs ----------------
def test_market_fs_has_bj():
    """北交所 fs 必须等于 fetcher._LISTING_FS 里那一段(项目已验证值)"""
    assert scorer.market_fs(["bj"]) == "m:0+t:81+s:2048"
    assert "m:0+t:81+s:2048" in scorer.market_fs(["hs", "cyb", "kcb", "bj"])
    assert "m:0+t:81+s:2048" not in scorer.market_fs(["hs", "cyb", "kcb"])


# ---------------- 市场归属(评分层 + picker 层必须同口径) ----------------
@pytest.mark.parametrize("code", BJ_CODES)
def test_in_markets_bj(code):
    assert scorer._in_markets(code, ["bj"]) is True
    assert scorer._in_markets(code, ["hs", "cyb", "kcb", "bj"]) is True
    assert scorer._in_markets(code, ["hs", "cyb", "kcb"]) is False      # 未勾选北交 → 排除
    assert pf.in_markets(code, ["bj"]) is True                          # picker 独立实现同口径
    assert pf.in_markets(code, ["hs", "cyb", "kcb"]) is False
    assert scorer._in_markets(code, []) is True                         # 空 = 不限制


def test_b_shares_still_excluded():
    """沪B 900xxx / 深B 200xxx 不属任何市场 —— 不得被 4/8/920 误命中"""
    for c in ("900015", "200015"):
        assert scorer._in_markets(c, ["hs", "cyb", "kcb", "bj"]) is False
        assert pf.in_markets(c, ["hs", "cyb", "kcb", "bj"]) is False


def test_other_markets_unchanged():
    """沪深创科口径回归(不得因加 bj 而放松)"""
    assert scorer._in_markets("600825", ["hs"]) is True
    assert scorer._in_markets("600825", ["cyb", "bj"]) is False
    assert scorer._in_markets("300876", ["cyb"]) is True
    assert scorer._in_markets("688981", ["kcb"]) is True
    assert scorer._in_markets("689009", ["kcb"]) is True


# ---------------- 默认值 / 白名单 ----------------
def test_system_markets_default_includes_bj():
    assert fd.SYSTEM_MARKETS == ["hs", "cyb", "kcb", "bj"]


def test_validate_filters_accepts_and_defaults_bj():
    assert scorer.validate_filters({"markets": ["bj"]})["markets"] == ["bj"]
    # 缺省(前端不传) → 含 bj
    assert scorer.validate_filters({})["markets"] == ["hs", "cyb", "kcb", "bj"]
    # 非法键被丢, 合法键保留
    assert scorer.validate_filters({"markets": ["hs,xx,bj"]})["markets"] == ["hs", "bj"]


# ---------------- 涨停幅度(北交所 30%) ----------------
def test_limit_pct_bse_30():
    assert scorer.limit_pct("920267", "鑫汇科", 10) == 0.30
    assert scorer.limit_pct("830799", "北交老段", 10) == 0.30
    assert scorer.limit_pct("430047", "老三板", 10) == 0.30
    assert scorer.limit_pct("689009", "科创CDR", 10) == 0.20     # 本次一并补 689
    assert scorer.limit_pct("600825", "新华传媒", 10) == 0.10
    assert scorer.limit_pct("300876", "创业板", 10) == 0.20
    assert scorer.limit_pct("920267", "ST北交", 10) == 0.05


def test_limit_pct_matches_snapshot_judge():
    """scorer.limit_pct 与 auction_snapshot.zt_limit_pct 必须同口径(两处等价实现, 需同步)

    注意: 名字必须不含 "ST" 字样 —— `limit_pct` 以 `"ST" in name` 判 ST(5%),
    否则会误判成 0.05 而与本用例无关地红(首版就踩了这个坑)。
    """
    from app.services import auction_snapshot as A
    for c in ("600825", "000678", "300876", "301190", "688981", "689009",
              "920267", "830799", "430047", "871981", "889999"):
        assert scorer.limit_pct(c, "普通股", 10) == A.zt_limit_pct(c), c


# ---------------- 源码级: 排除点已撤销 / 该保留的仍保留 ----------------
def test_source_no_longer_drops_bse():
    import inspect
    from app.services import auction_snapshot as A, concept_refresh as C
    a = inspect.getsource(A)
    assert "not scorer.is_bse" not in a, "快照采集层的北交所排除未撤销"
    c = inspect.getsource(C)
    assert "not scorer.is_bse" not in c, "概念层的北交所排除未撤销"


def test_sector_rotation_still_excludes_bse():
    """题材/板块成分股视图本轮**刻意不动** —— 若将来要一并纳入, 记得同步改本用例与前端说明"""
    import inspect
    from app.services import sector_rotation as S
    assert "not scorer.is_bse(c)" in inspect.getsource(S)


# ---------------- 行情取数的北交所 secid / 腾讯符号(2026-09-29 修) ----------------
def test_secid_and_tencent_symbol_for_bse():
    """🔴 920 段首字符是 9: 东财必须 0. / 腾讯必须 bj(此前被当沪市 1./sh ⇒ 永远取不到)。

    北交所判断必须先于沪市(6/9)判断; 沪B 900xxx 仍属沪市。
    """
    from app.services import fetcher as F
    assert F._secid("920779") == "0.920779"          # 武汉蓝电(北交所新段)
    assert F._secid("830799") == "0.830799"          # 北交老段
    assert F._secid("430047") == "0.430047"          # 老三板
    assert F._secid("600519") == "1.600519"          # 沪主板
    assert F._secid("900015") == "1.900015"          # 沪B(9 开头但非 92) 仍是沪市
    assert F._secid("000002") == "0.000002"          # 深主板
    assert F._tencent_symbol("920779") == "bj920779"
    assert F._tencent_symbol("830799") == "bj830799"
    assert F._tencent_symbol("430047") == "bj430047"
    assert F._tencent_symbol("600519") == "sh600519"
    assert F._tencent_symbol("900015") == "sh900015"
    assert F._tencent_symbol("000002") == "sz000002"


def test_tencent_prefix_has_single_truth_source():
    """腾讯符号只允许走 _tencent_symbol —— 不许再内联 `prefix = "sh" if ...`"""
    import inspect
    from app.services import fetcher as F
    src = inspect.getsource(F)
    assert 'prefix = "sh" if code.startswith' not in src, "又有内联腾讯前缀判断(北交所会漏)"
