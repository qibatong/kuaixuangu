# -*- coding: utf-8 -*-
"""9:15 竞价快照采集时序修复测试(2026-09-07 主人反馈"竞价封单表格 9:15 列为空")

根因: 9_15 快照在 **09:15:02** 采集 —— 集合竞价刚开始, 行情源(东财/腾讯)竞价字段
      尚未生成 → 实测当日 70% 的票 bid_change=0(对照 9:20 仅 28%) → 一字板/强势票
      的竞价额与封单拿不到, 表格 9:15 列大片空白(昨日 9/4 同样 65%, 系统性非偶发)。

修复: ① 9_15 采集延后到 9:15:30 后 ② 采集后校验零值率, >60% 则窗口内重采
      ③ 窗口从 9:15-9:16 扩到 9:15-9:17(留出重采时间)
"""
import pytest


def _insert(conn, date, tp, code, bid_change):
    conn.execute(
        "INSERT OR REPLACE INTO snapshot_bid(date, time_point, code, bid_change, bid_amt, "
        "ts, name, bid_buy_amt, float_mv, free_mv, board) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (date, tp, code, bid_change, 0.0, 1788743702, "股" + code, 0.0, 1e10, 1e10, ""))


@pytest.fixture
def conn():
    from app.db import database
    c = database.get_conn()
    yield c
    c.execute("DELETE FROM snapshot_bid WHERE date='2099-01-02'")
    c.commit()
    c.close()


def test_zero_chg_rate_counts_only_zero_bid_change(conn):
    """零值率 = bid_change=0(含 NULL) 占比"""
    from app.services import auction_snapshot as A

    # 样本需 >=100(实现里小于 100 条视为异常采集不判断)
    for i in range(100):
        _insert(conn, '2099-01-02', '9_15', '60%04d' % i, 0.0)       # 100 只未就绪
    for i in range(100, 200):
        _insert(conn, '2099-01-02', '9_15', '60%04d' % i, 5.0)       # 100 只有涨幅
    conn.commit()

    rate = A._zero_chg_rate('2099-01-02', '9_15')
    assert abs(rate - 0.5) < 1e-6, f"应为 50%, 实际 {rate}"


def test_zero_chg_rate_ignores_tiny_sample(conn):
    """样本 <100 条不判断(异常采集不触发重采)"""
    from app.services import auction_snapshot as A

    for i in range(3):
        _insert(conn, '2099-01-02', '9_20', '60%04d' % i, 0.0)
    conn.commit()
    assert A._zero_chg_rate('2099-01-02', '9_20') == 0.0


def test_zero_chg_rate_triggers_reread_threshold(conn):
    """70% 未就绪 → 超过 0.6 阈值, 应触发重采判断(True)"""
    from app.services import auction_snapshot as A

    for i in range(70):
        _insert(conn, '2099-01-02', '9_15', '6%05d' % i, 0.0)
    for i in range(70, 100):
        _insert(conn, '2099-01-02', '9_15', '6%05d' % i, 3.0)
    conn.commit()
    assert A._zero_chg_rate('2099-01-02', '9_15') > 0.6, "70% 零值应超过重采阈值"


def test_9_15_window_allows_reread():
    """9_15 窗口须扩到 9:17(留出重采时间; 原 9:16 结束时重采来不及)"""
    from app.services import auction_snapshot as A

    start, end = A.TIME_POINTS["9_15"]
    assert start == 9 * 60 + 15
    assert end >= 9 * 60 + 17, f"窗口结束 {end} 应 >= 9:17({9*60+17}) 以支持数据未就绪重采"


def test_scheduler_defers_9_15_and_rereads():
    """调度: 9:15:05 后首采(不是整点 00 秒, 数据未生成) + 未就绪重采
    (源码级断言, 防回归被误删; 阈值用 5 秒而非固定 30 秒 —— 主人要求尽量早)"""
    import inspect
    from app.services import auction_snapshot as A

    src = inspect.getsource(A._scheduler_loop)
    assert 'tp == "9_15" and g.tm_sec < 5' in src, "缺少 9:15:05 首采门槛(数据未生成保护)"
    assert '_zero_chg_rate' in src, "缺少数据未就绪重采逻辑"


class TestKplSealNotBlockedByBidChange:
    """开盘啦真实封单不应被东财 bid_change=0 误清零(9:15 列空白的另一半原因)"""

    def _run(self, monkeypatch, bid_change):
        from app.services import auction_snapshot as A
        from app.services import kpl
        from app.db import database

        monkeypatch.setattr(A, "_bj_date", lambda: "2099-01-02")
        # 模拟行情 map: 该票竞价涨幅未生成(东财 9:15 常见)
        monkeypatch.setattr(A, "_fetch_market_map", lambda full=False: {
            "600108": {"bid_change": bid_change, "bid_amt": 0.0, "name": "亚盛集团",
                       "bid_buy_amt": 0.0, "float_mv": 1e10, "free_mv": 1e10, "board": ""}})
        # 开盘啦委买榜返回真实封单 5 亿
        monkeypatch.setattr(kpl, "fetch_bid_seal", lambda: [
            {"code": "600108", "name": "亚盛集团", "bidSealAmt": 5.0e8, "board": "农业"}])
        monkeypatch.setattr(kpl, "clear_cache", lambda: None)

        n = A.snapshot_at("9_15", force=True)
        conn = database.get_conn()
        row = conn.execute(
            "SELECT bid_buy_amt, bid_change FROM snapshot_bid WHERE date='2099-01-02' AND code='600108'"
        ).fetchone()
        conn.execute("DELETE FROM snapshot_bid WHERE date='2099-01-02'")
        conn.commit()
        conn.close()
        return n, row

    def test_seal_kept_when_bid_change_not_ready(self, monkeypatch):
        """9:15 东财涨幅未生成(bid_change=0) 但开盘啦有封单 → 必须保留封单"""
        n, row = self._run(monkeypatch, 0.0)
        assert n >= 1, "应成功落库"
        assert row is not None, "应有落库行"
        assert abs((row[0] or 0) - 5.0e8) < 1, f"封单应保留 5 亿, 实际 {row[0]}"

    def test_seal_kept_when_zt(self, monkeypatch):
        """涨停时同样保留(回归)"""
        n, row = self._run(monkeypatch, 10.0)
        assert row is not None and abs((row[0] or 0) - 5.0e8) < 1
