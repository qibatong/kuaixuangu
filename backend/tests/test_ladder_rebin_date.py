# -*- coding: utf-8 -*-
"""2026-10-01 修复钉死: 连板天梯 rebin 必须用**数据所属交易日**取东财真实连板数。

背景（实测，非推测）: 2026-10-01 休市, 新华传媒 开盘啦 pid=5（「五板+」，分不出 6/7/8）、
东财 09-30 涨停池 limitUpDays=**7** ⇒ 页面却显示「5板」。
根因 = `build_zt_echelon` 用 `time.strftime('%Y-%m-%d')`（今天）去查东财池, 休市/盘前为空
⇒ `rebin_ladder` 原样返回 ⇒ 6/7/8 板全被压成「五板+」档。
"""
from datetime import datetime, timedelta

from app.services import kpl


def _prev_weekday(ds):
    d = datetime.strptime(ds, '%Y-%m-%d')
    while True:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            return d.strftime('%Y-%m-%d')


def test_prefers_today_when_pool_ready(monkeypatch):
    """盘中/盘后: 今天的池子已就绪 ⇒ 就用今天（不额外回看）"""
    today = kpl._bj_today()
    monkeypatch.setattr(kpl, 'real_limit_days', lambda d: {'600825': 7} if d == today else {})
    assert kpl._rebin_ref_date() == today


def test_falls_back_to_last_trading_day_when_today_empty(monkeypatch):
    """🔴 休市/盘前: 今天池空 ⇒ 回退最近有池子的交易日（否则 6/7/8 板被压成 5 板）"""
    today = kpl._bj_today()
    prev = _prev_weekday(today)
    monkeypatch.setattr(kpl, 'real_limit_days', lambda d: {'600825': 7} if d == prev else {})
    assert kpl._rebin_ref_date() == prev


def test_never_raises(monkeypatch):
    """东财异常时不得把整页带崩（返回今天, rebin 内部自会回退 pid 档）"""
    def boom(d):
        raise RuntimeError('eastmoney down')

    monkeypatch.setattr(kpl, 'real_limit_days', boom)
    assert kpl._rebin_ref_date() == kpl._bj_today()


def test_build_zt_echelon_passes_ref_date_not_today(monkeypatch):
    """钉死修复点: 传进 rebin_ladder 的日期 = _rebin_ref_date()（不是 time.strftime('%Y-%m-%d')）"""
    seen = []

    monkeypatch.setattr(kpl, 'fetch_ladder_all',
                        lambda: {5: [{'code': '600825', 'name': '新华传媒', 'seal': 1.0}]})
    monkeypatch.setattr(kpl, '_rebin_ref_date', lambda: '2026-09-30')
    monkeypatch.setattr(kpl, '_promote_rate', lambda ladders: {})

    def fake_rebin(d, date):
        seen.append(date)
        return {7: [{'code': '600825', 'name': '新华传媒', 'seal': 1.0, 'boardName': '并购重组'}]}

    monkeypatch.setattr(kpl, 'rebin_ladder', fake_rebin)
    out = kpl.build_zt_echelon()

    assert seen == ['2026-09-30'], seen
    assert out['stat']['maxLadder'] == 7, out['stat']
    assert out['stat']['spaceDragon'] == '新华传媒', out['stat']
    assert [L['ladder'] for L in out['ladders']] == [7], out['ladders']
    assert out['boards'][0]['maxLadder'] == 7, out['boards']
