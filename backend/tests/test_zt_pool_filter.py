# -*- coding: utf-8 -*-
"""昨涨停筛选(limitUp)判据测试 — 2026-09-07 修复

背景: is_first_board 原只认 f103 概念标签("昨日涨停/连板")。东财被墙走腾讯全市场兜底时,
      腾讯映射行**无 f103** → is_first_board 恒 False → 「不勾选剔除昨涨停」失效
      (勾不勾结果一样, 主人 9/7 反馈)。且 1104ff7 一度误改成 XOR("勾=只看昨涨停")
      导致腾讯期勾选直接空(主人 9/7 再次反馈) → 主人确认正确语义:
      **勾选 = 把昨日涨停/连板股也包含进结果; 不勾 = 剔除这类票**(不是"只看")。
修复: ① limitUp 判断恢复"不勾才剔除" ② is_first_board 优先 push2ex 昨涨停池名单
      (fetcher.get_yesterday_zt_codes, 与数据源无关, 腾讯兜底行也能判),
      名单不可用降级 f103 概念标签。conftest 已把 get_yesterday_zt_codes 全局桩成 None。
"""
import pytest


def _row(code, f103=None, name=None):
    """模拟一条原始行情行(腾讯构造行无 f103 / 东财行有 f103)"""
    r = {"f12": code, "f14": name or "股" + code, "f2": 10.0, "f4": 9.5,
         "f5": 10000, "f17": 9.6, "f3": 5.0, "f32": 5.0, "f8": 3.0,
         "f21": 5e10, "f615": 5.0, "f616": 2e7, "f617": 2e6, "f630": 0}
    if f103 is not None:
        r["f103"] = f103
    return r


class TestIsFirstBoardZT:
    def test_f103_concept_still_works(self, monkeypatch):
        """名单不可用(None) → 降级 f103 概念标签(既有东财行为不变)"""
        from app.services import scorer
        monkeypatch.setattr("app.services.fetcher.get_yesterday_zt_codes", lambda: None)
        assert scorer.is_first_board(_row("600000", f103="昨日涨停")) is True
        assert scorer.is_first_board(_row("600000", f103="昨日连板,人工智能")) is True
        assert scorer.is_first_board(_row("600001", f103="人工智能,CPO")) is False
        assert scorer.is_first_board(_row("600002", f103=None)) is False

    def test_zt_pool_primary_when_no_concept(self, monkeypatch):
        """腾讯兜底行(无 f103) + 名单可用 → 按名单判(修复核心场景)"""
        from app.services import scorer
        monkeypatch.setattr("app.services.fetcher.get_yesterday_zt_codes",
                            lambda: {"600354", "605577", "605580"})
        assert scorer.is_first_board(_row("600354")) is True    # 无 f103 也命中名单
        assert scorer.is_first_board(_row("600001")) is False


class TestFilterWithPool:
    """应用层: 勾/不勾昨涨停(主人 9/7 确认语义: 勾=包含, 不勾=剔除)"""

    def _filters(self, limit_up):
        return {"stSuspend": True, "limitUp": limit_up, "bidGt": 7.0,
                "probLt": 99.0, "confLt": 99.0, "floatMvFloor": 0, "floatMvGt": 1e99,
                "bidAmtFloor": 0, "priceGt": 1e99, "markets": ["hs", "cyb", "kcb"]}

    def _items(self):
        return [{"code": c, "name": n, "bidChange": 5.0, "probability": 99.0,
                 "confidence": 99.0, "circulationMV": 5e10, "price": 10.0, "bidAmt": 2e8,
                 "_raw": _row(c)} for c, n in
                [("600354", "敦煌种业"), ("600001", "普通股"), ("605577", "龙版传媒")]]

    def test_uncheck_excludes_yest_zt(self, monkeypatch):
        """不勾昨涨停 → 剔除昨日涨停票(腾讯兜底行无 f103 也能正确剔, 修复漏剔)"""
        from app.services import scorer
        monkeypatch.setattr("app.services.fetcher.get_yesterday_zt_codes",
                            lambda: {"600354", "605577"})
        out = scorer.apply_filters(self._items(), self._filters(False))
        assert [x["code"] for x in out] == ["600001"], "不勾应只剩非昨涨停票"

    def test_check_includes_yest_zt(self, monkeypatch):
        """勾选昨涨停 → 全部保留(普通票 + 昨涨停票都在, 非"只看昨涨停")"""
        from app.services import scorer
        monkeypatch.setattr("app.services.fetcher.get_yesterday_zt_codes",
                            lambda: {"600354", "605577"})
        out = scorer.apply_filters(self._items(), self._filters(True))
        assert {x["code"] for x in out} == {"600354", "600001", "605577"}, \
            "勾选应包含昨涨停票且不排除普通票"

    def test_check_uncheck_differ_only_by_zt(self, monkeypatch):
        """勾与不勾的结果差集 = 恰好是昨日涨停名单内票"""
        from app.services import scorer
        pool = {"600354", "605577"}
        monkeypatch.setattr("app.services.fetcher.get_yesterday_zt_codes", lambda: pool)
        checked = {x["code"] for x in scorer.apply_filters(self._items(), self._filters(True))}
        unchecked = {x["code"] for x in scorer.apply_filters(self._items(), self._filters(False))}
        assert checked - unchecked == pool
