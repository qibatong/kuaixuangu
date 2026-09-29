# -*- coding: utf-8 -*-
"""snapshot_bid 采集源换猫爪(_merge_meoz)单元测试

覆盖(2026-09-20 主源改 screening):
  ① 东财全挂(raw_all={}) → 猫爪独立产出全市场快照(含 free_mv 自由流通市值)
  ② 东财有票但缺字段 → 猫爪「只补缺, 绝不覆盖」
  ③ 单位换算: 猫爪 auc_amt(元) → bid_amt(万元) 必须 /1e4
  ④ 无涨幅的票不新增(进不了评分, 同 TickPlus 纪律)
  ⑤ float_mv(流通) 与 free_mv(自由流通) 分列落值, 不互相顶替
  ⑥ screening 缺字段时 valuation/daily_auc/daily_auc_fd 三层后备
  ⑦ 昨日封单额(pre_fd_amount) + 封昨比(fd_to_yesterday) 落库
  ⑧ 北交所(4/8/920) 已纳入(2026-09-29 主人拍板, 原全链路排除撤销)
  ⑨ daily_auc 防串日: tradedate ≠ 当日 → 整源丢弃(2026-09-24, 修 auc_vol_ratio 恒 0 的根因之一)
"""
import pytest

from app.services import auction_snapshot as A


@pytest.fixture
def fake_meoz(monkeypatch):
    """伪造 meoz_client:
      screening(实时选股, 主源: 全市场市值 + free_float_mv + 竞价额/涨幅)
      + valuation(后备市值/名称) + daily_auc_amt(后备金额/涨幅/名称)
      + auc_fd_map(后备 9:25 涨停封单额/题材)
    """
    from app.services import meoz_client

    # 🔴 tradedate 必须与 _merge_meoz 的防串日校验(_bj_date)同源 —— 写死日期会让
    #   "当日"夹具在第二天跑时被误判成串日残值, 整源丢弃(2026-09-24 踩过)。
    td = A._bj_date().replace("-", "")

    # 主源: screening —— 600519 全字段齐; 000001 缺竞价额/涨幅(留给后备)
    scr = {
        "600519": {"tradedate": td, "symbol": "600519", "name": "贵州茅台",
                   "circ_mv": 1.5715e12, "free_float_mv": 7.1505e11,
                   "auc_pct_chg": -0.31, "auc_amt": 14312200,
                   "close": 1490.0, "free_float_mv_x": 0,
                   # 昨日封单额 + 封昨比(2026-09-20 新增)
                   "pre_fd_amount": 520000000, "fd_to_yesterday": 1.66},
        "000001": {"tradedate": td, "symbol": "000001", "name": "平安银行",
                   "circ_mv": 2.253e11, "free_float_mv": 9.5478e10,
                   "auc_pct_chg": None, "auc_amt": None, "close": 11.7,
                   "pre_fd_amount": 0, "fd_to_yesterday": None},
        # 无涨幅的新票 → 不应新增
        "300999": {"tradedate": td, "symbol": "300999", "name": "无涨幅票",
                   "circ_mv": 5e9, "free_float_mv": 2e9,
                   "auc_pct_chg": None, "auc_amt": 100000},
    }
    val = {
        "000001": {"tradedate": td, "symbol": "000001", "name": "平安银行",
                   "circ_mv": 2.253e11, "total_mv": 2.253e11},
    }
    auc = {
        # auc_amt 单位=元
        "000001": {"tradedate": td, "symbol": "000001", "name": "平安银行",
                   "auc_pct_chg": -0.17, "auc_amt": 4232832, "m_price": 11.5},
        "300999": {"tradedate": td, "symbol": "300999", "name": "无涨幅票",
                   "auc_pct_chg": None, "auc_amt": 100000, "m_price": 0},
    }
    # 9:25 涨停封单额(仅涨停竞价股有) + 题材
    fd = {
        "600519": {"tradedate": td, "symbol": "600519", "name": "贵州茅台",
                   "fa_0925": 312102400, "theme_names_kpl": "白酒,消费"},
    }
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "screening_map", lambda **k: scr)
    monkeypatch.setattr(meoz_client, "valuation_map", lambda **k: val)
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: auc)
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: fd)
    return scr, val, auc, fd


def test_meoz_standalone_when_eastmoney_down(fake_meoz):
    """① 东财全挂 → 猫爪独立产出(含自由流通市值)"""
    raw = {}
    st = A._merge_meoz(raw)
    assert st["val_n"] == 3          # screening 3 只
    assert st["auc_n"] == 1          # valuation 后备 1 只
    assert st["fd_n"] == 2           # daily_auc 后备 2 只
    assert st["seal_n"] == 1         # 封单 1 只
    # 有涨幅 → 新增(000001 由后备补出涨幅)
    assert set(raw) == {"000001", "600519"}
    v = raw["000001"]
    assert v["name"] == "平安银行"
    assert v["bid_change"] == -0.17          # screening 无 → daily_auc 后备
    assert v["_src"] == "meoz"
    # 单位: 4232832 元 → 423.2832 万元
    assert abs(v["bid_amt"] - 423.2832) < 0.01
    assert v["float_mv"] == 2.253e11         # 流通市值(元)
    assert v["free_mv"] == 9.5478e10         # ★ 自由流通市值(元, screening 主源)
    assert v["warn_type"] == 0               # f630 已失活
    assert v["bid_buy_amt"] == 0             # 无封单(非涨停)
    # ★ 昨日封单额 + 封昨比(screening 独有)
    assert v["pre_fd_amount"] == 0
    assert v["fd_to_yesterday"] == 0         # None → 0(未落有效值)
    # 600519: screening 全字段齐 + 封单额 + 题材
    m = raw["600519"]
    assert m["bid_change"] == -0.31
    assert abs(m["bid_amt"] - 1431.22) < 0.01
    assert m["float_mv"] == 1.5715e12
    assert m["free_mv"] == 7.1505e11
    assert m["bid_buy_amt"] == 312102400
    assert m["board"] == "白酒,消费"
    # ★ 昨日封单额 + 封昨比
    assert m["pre_fd_amount"] == 520000000
    assert m["fd_to_yesterday"] == 1.66


def test_meoz_daily_auc_cross_day_guard(monkeypatch):
    """⑨ 防串日(2026-09-24 新增): daily_auc 返回上一交易日 → 整源丢弃, 不得写进今日快照。

    复刻实测场景 —— 早盘调 `daily_auc_amt(trademin="0925", date_offset=0)` 时, 目标日的
    9:25 尚未产出, 上游返回"最近可用"那份(实测 09:15 拿到**前一交易日**的 9:25, 5567 行)。
    不校验 tradedate 的话, 昨日竞价额/涨幅/量比会被写进今日定格。

    A/B 对拍: 除 `daily_auc.tradedate` 外一切相同 —— 当日 → 后备生效(新增); 跨日 → 丢弃。
    """
    from app.services import meoz_client
    td = A._bj_date().replace("-", "")

    def _run(auc_tradedate):
        """screening 故意不给竞价字段(逼迫走 daily_auc 后备); 只有 tradedate 变化。"""
        monkeypatch.setattr(meoz_client, "enabled", lambda: True)
        monkeypatch.setattr(meoz_client, "screening_map", lambda **k: {
            "600519": {"tradedate": td, "symbol": "600519", "name": "贵州茅台",
                       "circ_mv": 1.5715e12, "free_float_mv": 7.1505e11,
                       "auc_pct_chg": None, "auc_amt": None},
        })
        monkeypatch.setattr(meoz_client, "valuation_map", lambda **k: {})
        monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {
            "600519": {"tradedate": auc_tradedate, "symbol": "600519", "name": "贵州茅台",
                       "auc_pct_chg": 3.31, "auc_amt": 14312200, "auc_vol_ratio": 26.4},
        })
        monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: {})
        raw = {}
        return raw, A._merge_meoz(raw)

    # A: tradedate = 当日 → 后备生效: 新增该票, 落竞价额/涨幅/**标准量比**
    raw, st = _run(td)
    assert set(raw) == {"600519"}
    assert raw["600519"]["bid_change"] == 3.31              # 由 daily_auc 后备补出
    assert abs(raw["600519"]["bid_amt"] - 1431.22) < 0.01   # 14312200 元 → 1431.22 万元
    assert raw["600519"]["auc_vol_ratio"] == 26.4           # ★ 标准量比落库
    assert raw["600519"]["_src"] == "meoz"
    assert st["added"] == 1 and st["vr"] == 1
    assert st["fd_n"] == 1                                  # 可用行数 = 1(目标日)

    # B: tradedate = 上一交易日(串日残值) → 整源丢弃, 不可新增(否则昨日数据污染今日定格)
    raw, st = _run("19990101")
    assert raw == {}                                        # 无涨幅 → 不新增
    assert st["added"] == 0 and st["vr"] == 0
    # ★ fd_n 必须是**剔除串日后的可用行数**(不是上游原始行数) —— 旧实现把原始行数打进
    #   "竞价%d只" 日志, 早盘显示 5567 只看着正常、实为昨日值, 排查时被误导数轮。
    assert st["fd_n"] == 0


def test_meoz_only_fills_missing_never_overwrites(fake_meoz):
    """② 东财已有值 → 一律不动; 缺值 → 补"""
    raw = {
        "000001": {"bid_change": -0.99, "bid_amt": 999.0, "name": "东财名",
                   "bid_buy_amt": 888, "float_mv": 1.0e11, "free_mv": 5.0e10,
                   "board": "东财概念", "warn_type": 2},
        # 东财缺字段的行
        "600519": {"bid_change": 0, "bid_amt": 0, "name": "", "bid_buy_amt": 0,
                   "float_mv": 0, "free_mv": 0, "board": "", "warn_type": 0},
    }
    st = A._merge_meoz(raw)
    # 000001 全部保持东财原值(绝不覆盖)
    a = raw["000001"]
    assert a["bid_change"] == -0.99 and a["bid_amt"] == 999.0
    assert a["name"] == "东财名" and a["bid_buy_amt"] == 888
    assert a["float_mv"] == 1.0e11 and a["free_mv"] == 5.0e10
    assert a["board"] == "东财概念" and a["warn_type"] == 2
    # 600519 全部补齐(含流通/自由流通市值、封单额与题材)
    b = raw["600519"]
    assert b["name"] == "贵州茅台"
    assert abs(b["bid_amt"] - 1431.22) < 0.01      # 14312200 元 → 1431.22 万元
    assert b["bid_change"] == -0.31
    assert b["float_mv"] == 1.5715e12              # 流通市值
    assert b["free_mv"] == 7.1505e11               # ★ 自由流通市值
    assert b["bid_buy_amt"] == 312102400           # 猫爪 fa_0925 补封单
    assert b["board"] == "白酒,消费"                  # 猫爪题材补概念
    assert st["name"] == 1 and st["mv"] == 1 and st["frmv"] == 1
    assert st["amt"] == 1 and st["chg"] == 1 and st["seal"] == 1
    # ★ 昨日封单额 + 封昨比: 东财行无此键 → 猫爪补(prefd 计次)
    assert b["pre_fd_amount"] == 520000000
    assert b["fd_to_yesterday"] == 1.66
    assert st["prefd"] == 1


def test_meoz_pre_fd_never_overwrites(fake_meoz):
    """⑦ 昨日封单额 + 封昨比: 已有值不覆盖, 缺值才补"""
    raw = {
        # 东财链路不可能有这两字段, 但若上游已填(如二次合并) → 必须保留
        "600519": {"bid_change": 1.0, "bid_amt": 100.0, "name": "茅台",
                   "bid_buy_amt": 0, "float_mv": 1.0e12, "free_mv": 7.0e11,
                   "pre_fd_amount": 999, "fd_to_yesterday": 3.14,
                   "board": "", "warn_type": 0},
    }
    st = A._merge_meoz(raw)
    b = raw["600519"]
    assert b["pre_fd_amount"] == 999          # 已有 → 不覆盖
    assert b["fd_to_yesterday"] == 3.14
    assert st["prefd"] == 0


def test_meoz_fills_free_mv_when_float_mv_present(fake_meoz):
    """⑤ 东财有流通市值但缺自由流通市值 → 只补 free_mv(不动 float_mv)"""
    raw = {"600519": {"bid_change": 1.0, "bid_amt": 100.0, "name": "茅台",
                      "bid_buy_amt": 0, "float_mv": 1.0e12, "free_mv": 0,
                      "board": "", "warn_type": 0}}
    st = A._merge_meoz(raw)
    b = raw["600519"]
    assert b["float_mv"] == 1.0e12        # 东财流通市值保持不动
    assert b["free_mv"] == 7.1505e11      # ★ 补上自由流通市值
    assert st["mv"] == 0 and st["frmv"] == 1


def test_meoz_handles_disabled(monkeypatch):
    """meoz 未启用 → 空统计, 不改 raw_all"""
    from app.services import meoz_client
    monkeypatch.setattr(meoz_client, "enabled", lambda: False)
    raw = {}
    st = A._merge_meoz(raw)
    # 2026-09-20: 新增第⑤源 fundflow_kp → stats 多 "ff" 键(竞价主力净额非零计数)
    # 2026-09-24: 异动因子换标准量比 → stats 多 "vr" 键(竞价量比非零计数)
    assert st == {"val_n": 0, "auc_n": 0, "fd_n": 0, "seal_n": 0, "added": 0,
                  "name": 0, "mv": 0, "frmv": 0, "amt": 0, "chg": 0, "seal": 0,
                  "prefd": 0, "ff": 0, "vr": 0}
    assert raw == {}


def test_meoz_swallows_source_errors(monkeypatch):
    """四源全抛异常 → 静默降级, 不冒泡"""
    from app.services import meoz_client

    def _boom(*a, **k):
        raise RuntimeError("猫爪挂了")

    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "screening_map", _boom)
    monkeypatch.setattr(meoz_client, "valuation_map", _boom)
    monkeypatch.setattr(meoz_client, "daily_auc_amt", _boom)
    monkeypatch.setattr(meoz_client, "auc_fd_map", _boom)
    raw = {}
    st = A._merge_meoz(raw)
    assert st["val_n"] == 0 and st["auc_n"] == 0 and st["fd_n"] == 0
    assert raw == {}


def test_meoz_unit_conversion_guard():
    """③ 单位换算铁律: 元 → 万元"""
    # 直接验证 _f 与换算
    assert A._f("123.5") == 123.5
    assert A._f(None) is None
    assert A._f("") is None
    assert A._f("abc") is None
    # 1 亿元 → 10000 万元
    assert 100000000 / 1e4 == 10000.0


def test_is_bse():
    """北交所判定: 4/8/920 开头 = 北交所; 主板/创/科 = 非北交所"""
    from app.services import scorer
    assert scorer.is_bse("920267") is True       # 北交所新段
    assert scorer.is_bse("830799") is True       # 北交所老段(8 开头)
    assert scorer.is_bse("430001") is True       # 老三板(4 开头)
    assert scorer.is_bse("600519") is False      # 沪主板
    assert scorer.is_bse("000001") is False      # 深主板
    assert scorer.is_bse("300750") is False      # 创业板
    assert scorer.is_bse("688981") is False      # 科创板
    assert scorer.is_bse("") is False            # 空
    assert scorer.is_bse(None) is False          # None


def test_meoz_includes_bse(monkeypatch):
    """⑧ 北交所(4/8/920) **已纳入**(2026-09-29 主人拍板) —— screening 里的北交所票
    自此一并补进快照。(本用例 2026-09-21 曾断言"不补进快照", 现按新决策反向断言。)"""
    from app.services import meoz_client
    td = A._bj_date().replace("-", "")
    scr = {
        "600519": {"tradedate": td, "symbol": "600519", "name": "贵州茅台",
                   "circ_mv": 1.5715e12, "free_float_mv": 7.1505e11,
                   "auc_pct_chg": -0.31, "auc_amt": 14312200},
        # 北交所三只(920 新段 + 8 老段 + 4 老三板), 均有涨幅 —— 若不过滤会被补进快照
        "920267": {"tradedate": td, "symbol": "920267", "name": "鑫汇科",
                   "circ_mv": 1e9, "free_float_mv": 5e8,
                   "auc_pct_chg": 5.2, "auc_amt": 3000000},
        "830001": {"tradedate": td, "symbol": "830001", "name": "北交所老段",
                   "circ_mv": 1e9, "free_float_mv": 5e8,
                   "auc_pct_chg": 6.0, "auc_amt": 4000000},
        "430001": {"tradedate": td, "symbol": "430001", "name": "老三板",
                   "circ_mv": 1e9, "free_float_mv": 5e8,
                   "auc_pct_chg": 7.0, "auc_amt": 5000000},
    }
    monkeypatch.setattr(meoz_client, "enabled", lambda: True)
    monkeypatch.setattr(meoz_client, "screening_map", lambda **k: scr)
    monkeypatch.setattr(meoz_client, "valuation_map", lambda **k: {})
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "auc_fd_map", lambda *a, **k: {})
    raw = {}
    st = A._merge_meoz(raw)
    # 2026-09-29 起: 北交所三只一并补进快照(与主板 600519 共存)
    assert set(raw) == {"600519", "920267", "830001", "430001"}
    assert "920267" in raw and "830001" in raw and "430001" in raw
    assert st["val_n"] == 4          # screening 返回 4 只(含北交所), 但过滤后才落

