# -*- coding: utf-8 -*-
"""自算竞价量比(第二级兜底)单测 —— 2026-09-29。

背景: 官方成品 `猫爪 daily_auc.auc_vol_ratio` 的 ready_after = 09:25:35, 而"定格那一枪"
更早(旧口径 09:25:20~49) ⇒ 该列历史上恒 0, 评分量比层静默退化。自算提供第二级来源:
    竞价量比 = **竞价成交量 ÷ 近 5 日平均每分钟成交量**

口径已用官方成品**反推**验证(只读, 未改数据): 2026-09-24 / 09-28 各 3076 只可比,
自算分母 ÷ 官方隐含分母 中位 0.9959、p10 0.9888、p90 1.0032 ⇒ 逐只一致(~0.4%)。

本文件锁 5 条**不可回退**的纪律(全部纯函数/打桩, **不出网**):
  ① 口径常量: 241 分钟基准、近 5 日、**不含**目标日;
  ② 只补 0: 已有官方值的行绝不被覆盖(SQL 里必须带 `auc_vol_ratio=0`);
  ③ 只今日: 历史日一律不动(历史数据不可变);
  ④ 分子纪律: 历史日**绝不**用 screening.vol(那是全天量), 串日残值丢弃;
  ⑤ 分母不足 5 日(新股/长期停牌)的票跳过 ⇒ 保持 0 = 不可用(不是"量比=0")。
"""
import calendar
import time

import pytest

from app.services import auction_snapshot as asnap
from app.services import meoz_client


# ---------------- 打桩工具 ----------------

class _Kv:
    def __init__(self, ini=None):
        self.kv = dict(ini or {})
        self.set_calls = []

    def get(self, k, default=None):
        return self.kv.get(k, default)

    def set(self, k, v, ttl=0):
        self.set_calls.append((k, ttl))
        self.kv[k] = v
        return True


class _Rows:
    def __init__(self, items):
        self._items = list(items)

    def fetchall(self):
        return [(c,) for c in self._items]

    @property
    def rowcount(self):
        return len(self._items)


class _FakeDB:
    """记录 UPDATE 的假库; `zero` = 当前 auc_vol_ratio 为 0 的代码(模拟 SELECT 结果)。"""

    def __init__(self, zero=None):
        self.zero = list(zero or [])
        self.updates = []
        self.sql = []

    def get_conn(self):
        outer = self

        class _C:
            def execute(self, sql, params=()):
                outer.sql.append(sql)
                return _Rows(outer.zero)

            def executemany(self, sql, seq):
                outer.sql.append(sql)
                outer.updates.extend(list(seq))
                return _Rows([r[-1] for r in seq])

            def commit(self):
                pass

            def close(self):
                pass

        return _C()


class _FakeTime:
    """固定"北京某时刻"的假 time 模块(只覆盖自算用到的三个入口)。"""

    def __init__(self, cst_ymdhm):
        # 输入按北京时间解释 ⇒ UTC = 北京时间 − 8h（用 timegm, 不受本机时区影响）
        self._sec = calendar.timegm(time.strptime(cst_ymdhm + ':00', '%Y-%m-%d %H:%M:%S')) - 8 * 3600

    def time(self):
        return self._sec

    def gmtime(self, s=None):
        return time.gmtime(self._sec if s is None else s)

    def strftime(self, fmt, t=None):
        return time.strftime(fmt, t)


_CST = '2026-09-29 09:20'          # 竞价窗口内(09:15~09:30)
_TODAY = '2026-09-29'


def _frozen(monkeypatch, cst=_CST):
    monkeypatch.setattr(asnap, "time", _FakeTime(cst))


def _daily(vols_by_code):
    """构造 daily_history_map 的返回值: {code: [{tradedate, vol}, ...]}"""
    return vols_by_code


# ---------------- ① 口径常量与窗口 ----------------

def test_caliber_constants_are_241_and_5():
    """241 = 9:30~11:30(121) + 13:00~15:00(120); 5 = 近 5 日 —— 只读对拍验证过, 不许散成字面量。"""
    assert asnap._VR_BARS == 241
    assert asnap._VR_WIN == 5


def test_den_formula_matches_hand_calc(monkeypatch):
    """den = 近5日成交量之和 ÷ (5 × 241) —— 手/分钟。"""
    monkeypatch.setattr(asnap, "store", _Kv())
    monkeypatch.setattr(meoz_client, "daily_history_map", lambda codes, days=0: {
        '600001': [{'tradedate': '20260928', 'vol': 500}, {'tradedate': '20260925', 'vol': 400},
                   {'tradedate': '20260924', 'vol': 300}, {'tradedate': '20260923', 'vol': 200},
                   {'tradedate': '20260922', 'vol': 100}],
    })
    den = asnap._vr_den_map(['600001'], _TODAY)
    assert den['600001'] == pytest.approx(1500 / (5 * 241))


def test_den_window_is_5_days_strictly_before_target(monkeypatch):
    """含目标日(盘中那根 K)会把分子算进分母 —— 必须严格 < 目标日, 且取最近 5 天。"""
    monkeypatch.setattr(asnap, "store", _Kv())
    monkeypatch.setattr(meoz_client, "daily_history_map", lambda codes, days=0: {
        '600001': [
            {'tradedate': '20260929', 'vol': 999999},      # 目标日(今日盘中) → 必须排除
            {'tradedate': '20260928', 'vol': 500}, {'tradedate': '20260925', 'vol': 400},
            {'tradedate': '20260924', 'vol': 300}, {'tradedate': '20260923', 'vol': 200},
            {'tradedate': '20260922', 'vol': 100},
            {'tradedate': '20260921', 'vol': 88888},       # 第 6 天(更早) → 必须排除
        ],
    })
    den = asnap._vr_den_map(['600001'], _TODAY)
    assert den['600001'] == pytest.approx(1500 / (5 * 241))


def test_den_skips_stock_with_fewer_than_5_days(monkeypatch):
    """新股/长期停牌 ⇒ 分母缺失 ⇒ 不乱算: 直接不出现在返回值里(调用方保持 0 = 不可用)。"""
    monkeypatch.setattr(asnap, "store", _Kv())
    monkeypatch.setattr(meoz_client, "daily_history_map", lambda codes, days=0: {
        '600001': [{'tradedate': '20260928', 'vol': 500}, {'tradedate': '20260925', 'vol': 400},
                   {'tradedate': '20260924', 'vol': 300}, {'tradedate': '20260923', 'vol': 200}],
        '600002': [{'tradedate': '20260928', 'vol': 0}, {'tradedate': '20260925', 'vol': 0},
                   {'tradedate': '20260924', 'vol': 0}, {'tradedate': '20260923', 'vol': 0},
                   {'tradedate': '20260922', 'vol': 0}],
    })
    den = asnap._vr_den_map(['600001', '600002'], _TODAY)
    assert den == {}


def test_den_cached_per_date(monkeypatch):
    """分母按日缓存(近 5 日窗口一天只变一次) —— 一天只真出网一次。"""
    store = _Kv()
    monkeypatch.setattr(asnap, "store", store)
    calls = {'n': 0}

    def _d(codes, days=0):
        calls['n'] += 1
        return {'600001': [{'tradedate': '2026092%d' % i, 'vol': 100} for i in range(1, 6)]}

    monkeypatch.setattr(meoz_client, "daily_history_map", _d)
    asnap._vr_den_map(['600001'], _TODAY)
    asnap._vr_den_map(['600001'], _TODAY)
    assert calls['n'] == 1
    assert ('vr:den:20260929', asnap._VR_DEN_TTL) in store.set_calls


# ---------------- ④ 分子纪律 ----------------

def test_num_prefers_official_auc_vol(monkeypatch):
    """有官方竞价量就用它(与官方分子完全同源), 且不再打 screening。"""
    _frozen(monkeypatch)
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **k: {'600001': {'tradedate': '20260929', 'auc_vol': 1000}})
    monkeypatch.setattr(meoz_client, "screening_map",
                        lambda *a, **k: pytest.fail("有官方竞价量时不该再打 screening"))
    assert asnap._vr_num_map(['600001'], _TODAY) == {'600001': 1000.0}


def test_num_drops_stale_tradedate(monkeypatch):
    """串日残值(昨日数据)必须丢弃 —— 否则会把昨日竞价量当今日(与 meoz_bid_ready 同纪律)。"""
    _frozen(monkeypatch)
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **k: {'600001': {'tradedate': '20260928', 'auc_vol': 1000}})
    monkeypatch.setattr(meoz_client, "screening_map", lambda *a, **k: {})
    assert asnap._vr_num_map(['600001'], _TODAY) == {}


def test_num_never_uses_screening_for_historical_date(monkeypatch):
    """历史日的 screening.vol 是**全天**量(会放大几百倍) ⇒ 历史日绝不允许用它。"""
    _frozen(monkeypatch)
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "screening_map",
                        lambda *a, **k: pytest.fail("历史日不得用 screening.vol 当竞价量"))
    assert asnap._vr_num_map(['600001'], '2026-09-24') == {}


def test_num_uses_screening_vol_inside_auction_window(monkeypatch):
    """今日竞价窗口内(连续竞价未开始) screening.vol 就是竞价成交量 ⇒ 用它顶上。"""
    _frozen(monkeypatch, '2026-09-29 09:20')
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "screening_map",
                        lambda *a, **k: {'600001': {'vol': 800, 'auc_amt': 123}})
    assert asnap._vr_num_map(['600001'], _TODAY) == {'600001': 800.0}


def test_num_rejects_screening_vol_after_open(monkeypatch):
    """9:30 开盘后 screening.vol 变成全天累计量 ⇒ 绝不能再当竞价量(哪怕日期是今天)。"""
    _frozen(monkeypatch, '2026-09-29 10:00')
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {})
    monkeypatch.setattr(meoz_client, "screening_map",
                        lambda *a, **k: pytest.fail("开盘后不得用 screening.vol 当竞价量"))
    assert asnap._vr_num_map(['600001'], _TODAY) == {}


# ---------------- ②③⑤ 回填纪律 ----------------

def test_self_refill_only_fills_zero_rows(monkeypatch):
    """只补 0: UPDATE 必须带 `auc_vol_ratio=0`, 且只对 SELECT 出来的零值行动手。"""
    _frozen(monkeypatch)
    db = _FakeDB(zero=['600001'])
    monkeypatch.setattr(asnap.database, "get_conn", db.get_conn)
    monkeypatch.setattr(asnap, "store", _Kv())
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **k: {'600001': {'tradedate': '20260929', 'auc_vol': 1500}})
    monkeypatch.setattr(meoz_client, "daily_history_map", lambda codes, days=0: {
        '600001': [{'tradedate': '2026092%d' % i, 'vol': 100} for i in range(1, 6)]})

    n_calc, n_upd, n_all = asnap.refill_vol_ratio_self(_TODAY)
    assert (n_calc, n_all) == (1, 1)
    assert len(db.updates) == 1
    val, date, point, code = db.updates[0]
    assert code == '600001' and date == _TODAY and point == '9_25'
    assert val == pytest.approx(1500 / (500 / (5 * 241)), rel=1e-6)
    assert any('auc_vol_ratio=0' in s for s in db.sql), "必须只补 0(绝不覆盖官方成品)"


def test_self_refill_no_zero_rows_means_no_write(monkeypatch):
    """全部行都已有值(官方已回填) ⇒ 一条 UPDATE 都不发。"""
    _frozen(monkeypatch)
    db = _FakeDB(zero=[])
    monkeypatch.setattr(asnap.database, "get_conn", db.get_conn)
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **k: pytest.fail("没有零值行时不该取数"))
    assert asnap.refill_vol_ratio_self(_TODAY) == (0, 0, 0)
    assert db.updates == []


def test_self_refill_refuses_historical_date(monkeypatch):
    """③ 只今日: 历史日一律不动 —— 不查库、不取数、不写库。"""
    _frozen(monkeypatch)
    monkeypatch.setattr(asnap.database, "get_conn",
                        lambda: pytest.fail("历史日不得访问数据库"))
    monkeypatch.setattr(meoz_client, "daily_auc_amt",
                        lambda *a, **k: pytest.fail("历史日不得取数"))
    assert asnap.refill_vol_ratio_self('2026-09-24') == (0, 0, 0)


def test_self_refill_skips_stock_without_denominator(monkeypatch):
    """⑤ 分母不足 5 日 ⇒ 该票跳过(保持 0 = 不可用), 但其它票照常回填。"""
    _frozen(monkeypatch)
    db = _FakeDB(zero=['600001', '600002'])
    monkeypatch.setattr(asnap.database, "get_conn", db.get_conn)
    monkeypatch.setattr(asnap, "store", _Kv())
    monkeypatch.setattr(meoz_client, "daily_auc_amt", lambda *a, **k: {
        '600001': {'tradedate': '20260929', 'auc_vol': 1500},
        '600002': {'tradedate': '20260929', 'auc_vol': 900},
    })
    monkeypatch.setattr(meoz_client, "daily_history_map", lambda codes, days=0: {
        '600001': [{'tradedate': '2026092%d' % i, 'vol': 100} for i in range(1, 6)],
        '600002': [{'tradedate': '20260928', 'vol': 100}],      # 新股: 只有 1 天
    })
    n_calc, n_upd, _ = asnap.refill_vol_ratio_self(_TODAY)
    assert n_calc == 1 and len(db.updates) == 1
    assert db.updates[0][-1] == '600001'


def test_self_refill_is_wired_into_snapshot_and_netfill():
    """挂钩必须真的在: ① 定格落库后立即回填; ② 补采调用点第二级兜底。"""
    import inspect
    src_dump = inspect.getsource(asnap.snapshot_at)
    assert 'refill_vol_ratio_self' in src_dump, "定格落库后必须立即自算回填"
    assert 'time_point == "9_25"' in src_dump, "只对 9_25 定格生效"
    src_sched = inspect.getsource(asnap._scheduler_loop)
    assert 'refill_vol_ratio_self' in src_sched, "补采调用点必须有第二级兜底"
