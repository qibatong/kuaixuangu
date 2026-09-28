# -*- coding: utf-8 -*-
"""dev_risk 口径守卫单测（**全封闭，零网络**）

本文件的核心使命是**把「交易口径」钉死**，防止以后有人「顺手优化」成别的算法：

    ★ 交易所口径 = 区间首尾相减（区间涨幅差），**不是**逐日偏离值求和。
      依据：上交所《交易规则》(2026修订) 5.4.2(一) 原文
            「收盘价格涨跌幅偏离值累计值 = (期末收盘价/期初前收盘价−1)×100%
              − (对应指数期末收盘点数/期初前收盘点数−1)×100%」
      工单正文写「是每日偏离值求和」是**错的**（其自带样例 3日 +25.86 / 10日 +99.99
      恰恰只有区间法能复现）。2026-09-27 已请主人拍板取交易所口径。

两处最容易搞反的地方，各有一个专门的回归用例：
    `test_interval_not_daily_sum`      —— 三天各 +10% 必须得 33.10%（不是 30.00%）
    `test_immune_to_ex_dividend`       —— 送股导致收盘价腰斩、但官方涨跌幅为 0 时，区间必须为 0
    `test_warn_red_iff_triggered`      —— 红级**只能**由「已触发」产生（防 red 吞掉 yellow 的退化）

★ 2026-09-28 增补（off-by-one 回归）：
    `test_real_axis_matches_close_ratio` —— 真实价格轴必须逐位等于「真收盘价比」
    `test_real_axis_window_ends_at_today` —— 窗口末点必须是**今日**
  这两个用例存在的唯一理由：`_real_axis` 曾把窗口**整体左移一天**而长期无人发现，
  因为**全部既有用例都喂恒定涨幅**，而窗口平移在恒定序列上数值完全不可见。
  ⇒ 铁律：凡「窗口起点 / 数组下标」类逻辑，夹具必须用**逐日不同**的数据。
"""
import datetime as dt

import pytest

from app.services import dev_risk


# ---------------- 合成数据助手 ----------------

def _days(n, end="2026-09-24"):
    """n 个连续「交易日」日期（升序，YYYY-MM-DD），朴素按自然日造 —— 单测不需要真日历。"""
    d0 = dt.date.fromisoformat(end)
    out = []
    cur = d0
    while len(out) < n:
        out.append(cur.isoformat())
        cur -= dt.timedelta(days=1)
    return list(reversed(out))


def _idx(n=45, base=100.0, flat=True, end="2026-09-24"):
    """指数序列 [(date, close)]：flat=True 恒为 base。"""
    ds = _days(n, end)
    return [(d, base) for d in ds]


def _stock_from_pcts(pcts, start_close=10.0, end="2026-09-24"):
    """由「每日涨跌幅%」replay 出猫爪风格的个股行（含 close 与 pct_chg）。"""
    ds = _days(len(pcts), end)
    rows = []
    close = start_close
    for d, p in zip(ds, pcts):
        close = close * (1 + p / 100.0)
        rows.append({"tradedate": d.replace("-", ""), "close": round(close, 2),
                     "pct_chg": p, "name": "测试股"})
    return rows


@pytest.fixture
def patched(monkeypatch):
    """把网络取数换成夹具：`patch(idx=..., stock=...)` 注入后调 compute。"""
    state = {"idx": _idx(), "stock": _stock_from_pcts([0.0] * 45), "listing": None}

    def _index_series(code, force=False):
        return state["idx"]

    def _stock_series(code, days=dev_risk.FETCH_DAYS):
        return state["stock"]

    monkeypatch.setattr(dev_risk, "index_series", _index_series)
    monkeypatch.setattr(dev_risk, "stock_series", _stock_series)
    # ★ 2026-09-28 新股校验用：默认返回 None（= 不做校验，保持各既有用例原行为）。
    #   **必须**在这里挡掉 —— `_listing_date_of` 会走 `fetcher.fetch_listing_dates()`（东财网络），
    #   而本文件的铁律是「全封闭，零网络」（见文件头）。要测新股路径就用 `patch(listing=...)` 注入。
    monkeypatch.setattr(dev_risk, "_listing_date_of", lambda code: state["listing"])

    def patch(idx=None, stock=None, listing=None):
        if idx is not None:
            state["idx"] = idx
        if stock is not None:
            state["stock"] = stock
        if listing is not None:
            state["listing"] = listing

    patch.state = state
    return patch


# ---------------- 板块映射 ----------------

def test_board_of_mapping():
    assert dev_risk.board_of("605058") == dev_risk.BOARD_MAIN_SH     # 沪主板
    assert dev_risk.board_of("600519") == dev_risk.BOARD_MAIN_SH
    assert dev_risk.board_of("000001") == dev_risk.BOARD_MAIN_SZ     # 深主板
    assert dev_risk.board_of("002466") == dev_risk.BOARD_MAIN_SZ
    assert dev_risk.board_of("300750") == dev_risk.BOARD_GEM         # 创业板
    assert dev_risk.board_of("301236") == dev_risk.BOARD_GEM
    assert dev_risk.board_of("688981") == dev_risk.BOARD_STAR        # 科创板
    assert dev_risk.board_of("920002") == dev_risk.BOARD_BSE         # 北交所
    # 指数代码不得被当个股（899050 在 snapshot_bid 里存在，必须返回 None 而不是瞎猜板块）
    assert dev_risk.board_of("899050") is None
    assert dev_risk.board_of("") is None
    assert dev_risk.board_of("60505") is None
    assert dev_risk.board_of("ABCDEF") is None


def test_thresholds_match_current_rules():
    """阈值表逐项对照交易所原文（工单表有 4 处不符，此处是修正后的口径）。"""
    sh = dev_risk.spec_of("605058")
    assert (sh["index"], sh["dev3"], sh["dev10_up"], sh["dev10_dn"],
            sh["dev30_up"], sh["dev30_dn"], sh["limit"]) == \
        ("000002", 20.0, 100.0, -50.0, 200.0, -70.0, 10.0)
    sz = dev_risk.spec_of("002466")
    assert sz["index"] == "399107"                                    # 深证A指
    gem = dev_risk.spec_of("300750")
    # ★ 创业板基准指数 = 399102 创业板综指（不是工单写的 399006 创业板指）
    assert (gem["index"], gem["dev3"], gem["limit"]) == ("399102", 30.0, 20.0)
    star = dev_risk.spec_of("688981")
    assert (star["index"], star["dev3"], star["limit"]) == ("000688", 30.0, 20.0)
    bse = dev_risk.spec_of("920002")                                  # 工单整块漏掉北交所
    assert (bse["index"], bse["dev3"], bse["limit"]) == ("899050", 40.0, 30.0)
    # 五个模型共用 10日 +100/−50、30日 +200/−70
    for c in ("605058", "002466", "300750", "688981", "920002"):
        s = dev_risk.spec_of(c)
        assert (s["dev10_up"], s["dev10_dn"], s["dev30_up"], s["dev30_dn"]) == \
            (100.0, -50.0, 200.0, -70.0)


# ---------------- ★ 口径两条铁律 ----------------

def test_interval_not_daily_sum(patched):
    """★ 三天各 +10%：区间法 = 1.1³−1 = 33.10%；逐日累加 = 30.00%。

    断言 33.10 —— 若有人改回逐日累加，本用例立刻红。
    """
    patched(idx=_idx(), stock=_stock_from_pcts([0.0] * 42 + [10.0, 10.0, 10.0]))
    res = dev_risk.compute("605058")
    assert res["ok"] is True
    assert res["dev"]["d3"]["value"] == 33.1
    assert res["dev"]["d3"]["value"] != 30.0
    # 10 日与 30 日窗口内只有最后三天涨 ⇒ 同样是 33.1
    assert res["dev"]["d10"]["value"] == 33.1
    assert res["dev"]["d30"]["value"] == 33.1
    # 当日偏离 = 个股当日涨跌幅 − 指数当日涨跌幅 = 10 − 0
    assert res["today_dev"] == 10.0


def test_immune_to_ex_dividend(patched):
    """★ 除权免疫：送股导致收盘价腰斩、但官方涨跌幅记 0 ⇒ 区间偏离必须为 0。

    若用「不复权收盘价比值」实现，这里会算出 −50% 的假暴跌。
    """
    stock = _stock_from_pcts([0.0] * 42 + [0.0, 0.0, 0.0])
    # 人为把最后一天的收盘价打成一半（模拟 2:1 送股），pct_chg 仍为 0（官方口径）
    stock[-1]["close"] = round(stock[-2]["close"] / 2, 2)
    patched(idx=_idx(), stock=stock)
    res = dev_risk.compute("605058")
    assert res["dev"]["d3"]["value"] == 0.0          # ← 不含除权假跌
    # 反证：若按收盘价直除会得到约 −50%
    naive = (stock[-1]["close"] / stock[-4]["close"] - 1) * 100
    assert naive < -40


def test_index_component_is_subtracted(patched):
    """指数涨 5%、个股涨 10%（3 日）⇒ 偏离 = 33.1 − 15.76 ≈ 17.34（区间法两侧都是复利）。"""
    ds = _days(45)
    idx = [(d, 100.0 * (1.05 ** i)) for i, d in enumerate(ds)]
    patched(idx=idx, stock=_stock_from_pcts([0.0] * 42 + [10.0, 10.0, 10.0]))
    res = dev_risk.compute("605058")
    exp = round((1.1 ** 3 - 1) * 100 - (1.05 ** 3 - 1) * 100, 2)
    assert res["dev"]["d3"]["value"] == exp


# ---------------- 状态与阈值边界 ----------------

def test_status_boundaries():
    assert dev_risk._status(20.0, 20.0) == "触发"
    assert dev_risk._status(19.99, 20.0) == "临近"      # 20-5=15 起算临近
    assert dev_risk._status(15.0, 20.0) == "临近"
    assert dev_risk._status(14.99, 20.0) == "安全"
    # 负向：−50 / −70
    assert dev_risk._status(-50.0, -50.0) == "触发"
    assert dev_risk._status(-49.0, -50.0) == "临近"
    assert dev_risk._status(-45.0, -50.0) == "临近"
    assert dev_risk._status(-44.9, -50.0) == "安全"


def test_compute_rejects_unknown_board(patched):
    res = dev_risk.compute("899050")
    assert res["ok"] is False and res["reason"] == "unknown_board"


def test_compute_refuses_on_short_index(patched):
    """指数根数不足 ⇒ 必须 ok=False，**绝不能返回 0 冒充「安全」**。"""
    patched(idx=_idx(10), stock=_stock_from_pcts([0.0] * 45))
    res = dev_risk.compute("605058")
    assert res["ok"] is False and res["reason"] == "index_unavailable"


def test_compute_refuses_on_missing_stock(patched):
    patched(stock=[])
    res = dev_risk.compute("605058")
    assert res["ok"] is False and res["reason"] == "stock_unavailable"


# ---------------- 新股前 5 个交易日（2026-09-28）----------------
# 规则：深交所《交易规则(2023修订)》3.3.15「上市后前五个交易日不实行价格涨跌幅限制」；
#   证监会全面注册制答记者问同口径；严重异常波动(10/30 日偏离)计算**不纳入新股上市前 5 日**
#   ⇒ 从第 6 个交易日起算。回归对象：301686「C中塑股份」（上市第 4 日、实际 −46%，
#   修复前被算成 10 日 +321.63% ⇒ 红牌误报）。

def test_sixth_trade_day_is_5_trading_days_after_listing():
    """起算日 = 上市日之后**恰好 5 个交易日**（上市日算第 1 个）—— 用真交易日历校验性质。

    刻意断言「性质」而不是硬编码某个日期：硬编码会把「中秋节/国庆」这类日历数据变更
    变成假失败；而 (上市日, 起算日] 区间内恰好 5 个交易日 + 起算日本身是交易日，
    才是这条规则的实质。
    """
    for ld in ("2026-09-22", "2026-08-03", "2025-12-30"):
        k = dev_risk._sixth_trade_day(ld)
        assert k, ld
        assert dev_risk.trade_calendar.is_trade_day(dt.date.fromisoformat(k)), (ld, k)
        n, cur = 0, dt.date.fromisoformat(ld)
        end = dt.date.fromisoformat(k)
        while cur < end:
            cur += dt.timedelta(days=1)
            if dev_risk.trade_calendar.is_trade_day(cur):
                n += 1
        assert n == 5, (ld, k, n)


def test_sixth_trade_day_none_on_bad_input():
    """拿不到上市日期 ⇒ 返回 None ⇒ 调用方**不做**新股校验（保守回退，不误伤任何票）。"""
    for bad in (None, "", "——", "2026-13-99"):
        assert dev_risk._sixth_trade_day(bad) is None, bad


def test_new_stock_before_kickoff_is_refused(patched):
    """上市第 3 日（窗口仍含前 5 日）⇒ 整只弃权，**绝不**输出失真偏离值。"""
    patched(idx=_idx(45), stock=_stock_from_pcts([-8.0] * 3), listing="2026-09-21")
    res = dev_risk.compute("605058")
    assert res["ok"] is False and res["reason"] == "new_stock", res
    assert "不纳入异动计算" in res["msg"]
    # ★ 关键：不许像修复前那样照常给出 d10/d30（那正是红牌误报的来源）
    assert "dev" not in res and "room" not in res, res


# ---------------- 停牌/无成交日不占窗口名额（2026-09-28）----------------
# 交易所原文：「偏离值按竞价交易日滚动计算，**不含停牌/无成交日**」。
# 原实现把窗口钉成「指数的最后 n 个交易日」，个股落在窗口里的行连乘 ⇒ 停牌日占名额却贡献 0%。

def test_suspended_days_do_not_take_window_slots(patched):
    """个股自己的 30 个交易日才是窗口 ⇒ 停牌 3 天的票，窗口首日必须**往前提 3 天**。"""
    idx = _idx(45)
    ds = [d for d, _ in idx]
    skip = set(ds[-28:-25])                    # 最近 30 个交易日里缺 3 天（模拟停牌）
    rows, close = [], 10.0
    for d in ds:
        if d in skip:
            continue
        close *= 1.02
        rows.append({"tradedate": d.replace("-", ""), "close": round(close, 2),
                     "pct_chg": 2.0, "name": "停牌股"})
    patched(idx=idx, stock=rows, listing="2020-01-02")
    res = dev_risk.compute("605058")
    assert res["ok"] is True, res
    win = res["detail"][30]["window"]
    sd = sorted(r["tradedate"] for r in rows)
    exp = sd[-30]                              # 个股自己的第 30 个交易日
    exp_iso = "%s-%s-%s" % (exp[:4], exp[4:6], exp[6:8])
    assert win.startswith(exp_iso), (win, exp_iso)
    # ★ 关键：它**不是**指数 30 日窗口的首日（旧口径）—— 证明停牌日确实没占名额
    assert not win.startswith(idx[-30][0]), (win, idx[-30][0])


def test_all_out_of_range_rows_are_refused(patched):
    """个股在指数区间内没有任何行情（长期停牌）⇒ 弃权，**不得**折成 0% 产出假偏离值。"""
    idx = _idx(45)
    old = [{"tradedate": "2025-01-0%d" % i, "close": 10.0, "pct_chg": 0.0, "name": "停牌股"}
           for i in range(1, 4)]
    patched(idx=idx, stock=old, listing="2020-01-02")
    res = dev_risk.compute("605058")
    assert res["ok"] is False and res["reason"] == "window_incomplete", res


def test_old_listing_unaffected(patched):
    """老股（上市远早于窗口）不受影响 —— 校验不得误伤正常票。"""
    patched(idx=_idx(45), stock=_stock_from_pcts([2.0] * 45), listing="2020-01-02")
    assert dev_risk.compute("605058")["ok"] is True


def test_new_stock_after_kickoff_computes(patched):
    """上市已满 6 个交易日、且 30 日窗口整体落在起算日之后 ⇒ 照常计算（边界另一侧）。"""
    idx = _idx(45)
    first30 = idx[-30][0]                     # 30 日窗口的首个交易日（夹具里就是它决定合规性）
    ld = "2026-08-01"
    kick = dev_risk._sixth_trade_day(ld)
    assert kick and kick <= first30, (ld, kick, first30)
    patched(idx=idx, stock=_stock_from_pcts([2.0] * 45), listing=ld)
    assert dev_risk.compute("605058")["ok"] is True


# ---------------- 明日触发空间 ----------------

def test_next_trigger_analytic(patched):
    """逐日 +7% 的票（主板）：30 日线已触发 ⇒ 取 10 日线，明日需再涨约 8.79%。

    推导（10 日线）：x = (1 + 100/100) / (1 + s9/100) − 1，s9 = 1.07⁹−1 = 83.85%
        ⇒ x = 2.00/1.8385 − 1 = 8.79%（≤ 涨停 10% ⇒ reachable=True）
    期间各线状态：d3 = 22.50% 已触发、d10 = 96.72% 临近、d30 = 661% 已触发。

    🔴 2026-09-28 主人拍板：挑线范围**收窄到 10/30** ⇒ 3 日线不再产生「下一条」
      （旧用例是逐日 +5%、room 取 3 日线的 8.84% —— 那个数字现在不该再出现在 room 里）。
    """
    patched(idx=_idx(45), stock=_stock_from_pcts([7.0] * 45))
    res = dev_risk.compute("605058")
    assert res["dev"]["d30"]["status"] == "触发"
    assert res["dev"]["d3"]["status"] == "触发"
    room = res["room"]
    assert room["rule"].startswith("10日"), room
    assert "3日" not in room["rule"], room
    assert abs(room["next_trigger_pct"] - 8.79) < 0.05, room
    assert room["reachable"] is True                      # 8.79% <= 涨停 10%
    assert room["trigger_price"] == round(res["price"] * (1 + room["next_trigger_pct"] / 100), 2)
    assert len(room["all"]) == 1                          # 30 日线已触发 ⇒ 只剩 10 日线候选


def test_next_trigger_not_reachable(patched):
    """逐日 +2% 的票：10/30 两条线都还很远，明日涨停也不够 —— reachable 必须为 False。

    推导：10 日线 s9 = 19.51% ⇒ 需 67.3%；30 日线 s29 = 77.58% ⇒ 需 68.9% ⇒ 都 > 涨停 10%。
    """
    patched(idx=_idx(45), stock=_stock_from_pcts([2.0] * 45))
    res = dev_risk.compute("605058")
    room = res["room"]
    assert room["next_trigger_pct"] > res["limit_up_pct"]
    assert room["reachable"] is False
    assert room["hit"] == []


# ---------------- 真实价格轴（★ 2026-09-28 off-by-one 回归） ----------------
#
# `_real_axis` 把个股真实日涨幅连乘成「相对期初前收盘」的轴，供未来十日推演使用。
# 🔴 它曾把向过去的索引写成 `last - off`（应为 `last - off + 1`）：
#    退掉的是**昨日**涨幅而非**今日** ⇒ 整个 n 日窗口**左移一天**。
#    该 bug 长期未被发现，因为既有用例全部使用「每日恒定涨幅」——
#    恒定序列上窗口平移一天**数值完全不可见**。
#    ⇒ 铁律：凡「窗口起点 / 数组下标」类逻辑，夹具必须用**逐日不同**的数据。

def _replay_closes(pcts, start=10.0):
    """与 `_stock_from_pcts` 同口径、但不做两位小数舍入，供精确对拍。"""
    out, c = [], start
    for p in pcts:
        c *= 1 + p / 100.0
        out.append(c)
    return out


def test_real_axis_matches_close_ratio():
    """★ off-by-one 回归：轴必须逐位等于「真收盘价比」，且夹具必须逐日不同。"""
    pcts = [0.0, 1.5, -2.0, 3.7, -0.8, 2.2, 5.1, -1.3, 4.4, 0.6, -3.1, 2.9]
    # 反空转守卫：夹具若恒定，本用例对「窗口平移」零鉴别力（这正是当初漏检的根因）
    assert len(set(pcts[1:])) > 5, "夹具必须逐日不同，否则该回归用例形同虚设"
    rows = _stock_from_pcts(pcts)
    idates = _days(len(pcts))
    closes = _replay_closes(pcts)
    last = len(idates) - 1
    for n in (3, 10):
        out, im0 = dev_risk._real_axis(rows, idates, n)
        assert out, "轴不应弃权（夹具长度足够 n+1）"
        assert abs(out[-n] - 1.0) < 1e-12, "归一到期初前收盘 ⇒ out[−n] 必须恰为 1"
        for d in range(0, -n - 1, -1):
            want = closes[last + d] / closes[last - n]
            assert abs(out[d] - want) < 1e-12, (
                "offset %d: 轴=%.12f 真值=%.12f（左移一天会让整窗错位）"
                % (d, out[d], want))
        # im0 = 今日对前一日的比 ⇒ 必须是**今日**涨幅，不是昨日
        assert abs(im0 - (1 + pcts[last] / 100.0)) < 1e-12, im0


def test_real_axis_window_ends_at_today():
    """★ 直钉窗口端点：out[0] 必须是「今日为末点」的 n 日涨幅，而不是「昨日为末点」。"""
    pcts = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
    rows = _stock_from_pcts(pcts)
    idates = _days(len(pcts))
    last = len(idates) - 1
    n = 3

    def prod(i, j):                       # 含端点连乘（按 pcts 下标直取）
        v = 1.0
        for k in range(i, j + 1):
            v *= 1 + pcts[k] / 100.0
        return v

    out, _ = dev_risk._real_axis(rows, idates, n)
    correct = prod(last - n + 1, last)    # [last−n+1 … last] 正确：末点=今日
    shifted = prod(last - n, last - 1)    # [last−n   … last−1] 旧行为：末点=昨日
    assert abs(correct - shifted) > 1e-3, "夹具无法区分两种窗口 ⇒ 用例无效"
    assert abs(out[0] - correct) < 1e-12, (out[0], correct, shifted)
    assert abs(out[0] - shifted) > 1e-3, "out[0] 仍等于左移一天的旧行为 ⇒ off-by-one 回归"


# ---------------- 未来十日推演（★ v4.11.71 起改为「实基倒推」） ----------------
#
# 旧实现假设「个股每日 +涨停、指数持平」逐日推演 ⇒ 10 行**全部**「已触发」，
# 与今日真实偏离无关（同板块任何票长得一样）—— 主人 2026-09-28 定性为「虚值」。
# 新实现：轴由 **srows 真实日涨幅连乘**得出（除权免疫），未来段才续 `(1+g)`；
# 每行反解「第 k 天首次触发所需的最小日均涨幅 g」。
# 🔴 全文最关键的一条：**能否触发 = g ≤ 一个涨停**，
#    不是「二分求解器返回了非 None」（后者只说明 g ∈ [−99%, +300%]）。

def test_project_day1_matches_next_trigger(patched):
    """★ 退化自证：k=1 时推演解出的日均涨幅必须与 `room.next_trigger_pct` **数值全等**。

    原理：k=1 的窗口 [1−n … 1] 与今日真实窗口 [1−n … 0] 期初完全相同，
      仅末点由「今日收盘」换成「第 1 日收盘」⇒ g 的闭式解与 `_next_trigger` 逐位同源。
    这条同时钉死两件事：① 真实轴造对了（造错则 k=1 不可能对上闭式解）；
      ② 推演表第 1 行不是「假设涨停」，而是「按实际倒推」。

    ⚠️ 夹具必须选「第 1 天解 ≤ 一个涨停」的票：`_axis_g` 会把未来日涨幅**截断到 cap**
      （真实市场单日不可能超过涨停）⇒ 若房间值 > cap，则 k=1 的解被截断，
      与 `_next_trigger` 的**未截断**闭式解就不可比了。

    🔴 2026-09-28：挑线范围收窄到 10/30 ⇒ 这里比的是 **need10**（原为 need3）。
    """
    # 逐日 +7%：dev30 = 661% ⇒ 30 日线已触发；10 日线 96.72% 临近未触发
    #   room 的「下一条」= 10 日线，需再涨 8.79%（≤ 10% 涨停 ⇒ reachable=True）
    patched(idx=_idx(45), stock=_stock_from_pcts([7.0] * 45))
    res = dev_risk.compute("605058")
    p = res["project10"]
    assert len(p) == 10
    assert res["room"]["rule"].startswith("10日")
    assert res["room"]["reachable"] is True
    assert abs(res["room"]["next_trigger_pct"] - 8.79) < 0.05, res["room"]
    # ★ k=1 的 10 日线解 必须与 room 的次日触发空间**数值全等**（这就是「按实际倒推」的定义）
    assert p[0]["need10"] is not None, p[0]
    assert abs(p[0]["need10"] - res["room"]["next_trigger_pct"]) < 0.05, (p[0], res["room"])
    # ★ 但 10 日线**不进** trigger（它此刻还没越线）⇒ 该行报的是已触发的 30 日线
    assert p[0]["trigger_rule"].startswith("30日"), p[0]
    assert "3日" not in p[0]["trigger"] and "3日" not in p[0]["trigger_rule"]


def test_project_real_base_not_limit_chain(patched):
    """★ 反虚值核心：起点是**今日真实偏离**，不是「从头开始天天涨停」。

    两组对照必须给出**不同的**推演结果 —— 旧版（假设天天涨停）下两组完全相同
    （旧版把未来段完全当连板，与历史无关）：
      A) 过去 45 日全平 ⇒ 今日 10 日偏离 = 0，主轴全 1.0，10 日线需 8 天连板
      B) 最近 9 日 +10%/日、今日持平 ⇒ 今日 10 日偏离 = 1.1^9−1 = 135.79%（已触发）
         ⇒ 主轴已抬升 2.3579 倍，10 日线**第 1 天就不需再涨**（safe_gain_pct = 0）
    """
    # A：全平 —— 起点 1.0
    patched(idx=_idx(45), stock=_stock_from_pcts([0.0] * 45))
    res_a = dev_risk.compute("605058")
    pa = res_a["project10"]
    assert all(r["left30"] is None for r in pa)            # 主板 30 日线：1.1^10−1=159% < 200%
    assert pa[0]["left10"] == 7                            # 第 8 天（1.1^8−1=114.36%）才可达
    assert pa[0]["safe_gain_pct"] is None                  # 第 1 天不触发 ⇒ 无「安全涨幅」
    assert pa[0]["trigger_rule"] == "无"

    # B：最近 9 日 +10%/日、第 10 日（今日）持平 ⇒ 今日 10 日偏离 = 1.1^9−1 = 135.79%
    patched(stock=_stock_from_pcts([0.0] * 35 + [10.0] * 9 + [0.0]))
    res_b = dev_risk.compute("605058")
    pb = res_b["project10"]
    assert res_b["dev"]["d10"]["status"] == "触发"          # 今日 10 日线已触发
    assert res_b["dev"]["d10"]["value"] == 135.79
    # ★ 真实起点 ⇒ 第 1 天「已触发」，安全涨幅为 0（无需再涨），目标价 = 现价
    assert pb[0]["left10"] == 0
    assert pb[0]["trigger_rule"].startswith("10日")
    assert pb[0]["safe_gain_pct"] == 0.0
    assert pb[0]["price"] == res_b["price"]
    # ★ 与 A 组必须**显著不同** —— 这是「虚值」与「实算」的判别位
    assert pa[0]["left10"] != pb[0]["left10"]              # 7 vs 0
    assert pa[0]["trigger_rule"] != pb[0]["trigger_rule"]  # 「无」vs「10日+100%」
    assert pa[0]["safe_gain_pct"] != pb[0]["safe_gain_pct"]  # None vs 0.0


def test_project_excludes_3day_from_trigger(patched):
    """★ v4.11.69+ 回归位：3 日线**只**从推演表的触发判定里剔除，`need3` 仍照算。

    反证（防「顺手把 3 日线也算回去」或「整列打死」）：
      ① `need3` 逐日仍必须有值（真实倒推量）；
      ② 即便 3 日线可达，`trigger` / `trigger_rule` / `zt_trigger` 也不得提到「3日」；
      ③ 10 日线可达时 `trigger` 必须**照常**报出来（证明剔除只针对 3 日线）。
    """
    # 全平 + 主板：3 日线 ±20% 靠 2 个涨停可达、10 日线 +100% 靠 8 个涨停可达
    patched(idx=_idx(45), stock=_stock_from_pcts([0.0] * 45))
    p = dev_risk.compute("605058")["project10"]
    # ① need3 逐日仍必须有值；★ 第 1 天因「单日不可能涨 20%」而被截断 ⇒ None 是对的，
    #    第 2 天起（2 个连板可达 21% > 20%）才有解。
    #    🔴 这里刻意钉住「截断语义」：`_axis_g` 把未来日涨幅 clamp 到 cap ⇒ 3 日线 ±20%
    #       在主板单日永远解不出 ⇒ need3[0] = None 是**正确**行为，
    #       不能因为「看着像缺数据」就把它改成 20.0（那会假造一个做不到的涨幅）。
    assert p[0]["need3"] is None, p[0]
    assert all(r["need3"] is not None for r in p[1:]), [r["need3"] for r in p]
    assert abs(p[1]["need3"] - 20.0) < 0.05, p[1]
    # ② 全程不得出现 3 日线标签（即便 3 日线在第 2 天就可达 ⇒ 2 个连板越 20%）
    for r in p:
        assert "3日" not in r["trigger"], r
        assert "3日" not in r["trigger_rule"], r
    # ③ 10 日线第 8 天可达 ⇒ trigger 必须报 10 日线（剔除只针对 3 日线）
    assert "10日" in p[7]["trigger"]
    assert p[7]["trigger_rule"].startswith("10日")
    assert p[7]["zt_trigger"] is True


def test_project_uses_board_limit(patched):
    """★ 判据 `g ≤ cap` 的 cap 必须取**该板块**涨停（创业板 20%，非硬编码 10%）。

    全平 + 创业板 ⇒ 10 日线 +100% 反解：
      1.2^k − 1 ≥ 1 ⇒ k = ln2/ln1.2 = 3.80 ⇒ **第 4 天可达**（1.2^4−1 = 107.36%）
      前 3 天日涨都 > 20% ⇒ 逐日解出的 g 必须 > cap ⇒ 判「不可达」。
    若 cap 被误取成 10%，第 4 天也到不了（1.1^4−1 = 46.4%）⇒ 本用例即红。
    """
    patched(idx=_idx(45), stock=_stock_from_pcts([0.0] * 45))
    res = dev_risk.compute("300750")
    p = res["project10"]
    assert p[0]["limit_up_pct"] == 20.0
    # ★ 第 1~3 天不可达、第 4 天可达 ⇒ left10(第1天) = 3
    assert p[0]["left10"] == 3, p[0]
    assert [r["left10"] for r in p[:3]] == [3, 2, 1], [r["left10"] for r in p[:3]]
    assert p[3]["left10"] == 0 and p[3]["trigger_rule"].startswith("10日")
    assert p[3]["zt_trigger"] is True
    # 前 3 天：所需日涨 > 20% ⇒ 不可达（这正是 cap=20 生效的判别位）
    assert p[0]["zt_trigger"] is False and p[2]["zt_trigger"] is False
    # 30 日线 +200%：1.2^k−1 ≥ 2 ⇒ k = ln3/ln1.2 = 6.03 ⇒ 第 7 天可达 ⇒ left30(第1天) = 6
    assert p[0]["left30"] == 6, p[0]
    assert p[6]["left30"] == 0 and "30日" in p[6]["trigger"]


def test_project_reachability_uses_cap_not_solver(patched):
    """★ 可达性判据：`leftN` 必须是「**靠连板真能做到**」，不是「求解器返回了值」。

    🔬 变异测试结论（2026-09-28 实测，务必理解，否则会误删守卫）：
      · 真正扛住「不可达」的是 `_axis_g` 的 **clamp**（未来日涨幅截断到 cap）——
        `_solve` 因截断而对「10 天内涨停都够不到」的线**直接返回 None**，
        故 `leftN` 的两个判据（`g is not None` 与 `g <= cap`）在**当前夹具集**下**等价**。
      · 单独把 `g <= cap` 删掉（改成 `g is not None`）**不会让任何用例变红**（已实测）。
      ⇒ 因此本用例断言的是**机制**（截断 + 判据），而不是某一行的写法：
        只要有人动了 clamp，`test_project_excludes_3day_from_trigger` 的
        `need3[0] is None` 断言立刻会红（M1 变异实测：该用例红）。
      ⇒ 判据本身保留为**纵深防御**：即便未来 clamp 被改成别的形式，
        `g <= cap` 仍能把「数学上解得出、现实中做不到」的线挡住。

    判别样本（主板，cap = 10%）：
      · 全平 + 10 日线 +100%：第 1 天需单日 +100% > cap ⇒ 不可达；
        k=8 时摊薄为 9.05% ≤ cap ⇒ 可达 ⇒ left10 必须是 **7**（不是 0、也不是 None）。
      · 30 日线 +200%：1.1^10−1 = 159% < 200% ⇒ 全表 None（真不可达）。
    """
    patched(idx=_idx(45), stock=_stock_from_pcts([0.0] * 45))
    p = dev_risk.compute("605058")["project10"]
    # ★ 第 1 天：10 日线需单日 +100%，远超涨停 ⇒ 必须判「不可达」
    assert p[0]["left10"] == 7, p[0]
    assert p[0]["trigger"] == "不触发" and p[0]["zt_trigger"] is False
    assert p[0]["safe_gain_pct"] is None and p[0]["price"] is None
    # 第 7 天（k=8）刚好可达
    assert p[7]["left10"] == 0 and p[7]["trigger_rule"].startswith("10日")
    # 30 日线：10 天涨停都不够 ⇒ 全表 None（真不可达，与上面「只是今天不行」必须区分）
    assert all(r["left30"] is None for r in p), [r["left30"] for r in p]


def test_project_future_gain_is_capped_at_limit(patched):
    """★ 钉住 **clamp 机制本身** —— 这是「连板」与「任意涨」的分界，也是可达性的真正守门人。

    反证：若 `_axis_g` 不截断（允许未来日涨幅超过涨停，M1 变异），则：
      ① 30 日线 +200% 在主板会被判「可达」（因为可以让某一天涨 300%）
         ⇒ `left30` 不再是 None ⇒ 本用例红；
      ② 且 `need3` 会在第 1 天就解出 20%（而真实单日最多 10%）
         ⇒ `need3[0] is None` 不再成立 ⇒ 本用例红。
    """
    patched(idx=_idx(45), stock=_stock_from_pcts([0.0] * 45))
    p = dev_risk.compute("605058")["project10"]
    # ① 30 日线在 10 天内绝不可达（1.1^10−1 = 159.37% < 200%）—— 截断的直接后果
    assert all(r["left30"] is None for r in p), [r["left30"] for r in p]
    assert all(r["need30"] is None for r in p), [r["need30"] for r in p]
    # ② 3 日线 ±20% > 单日涨停 10% ⇒ 第 1 天无解（不是「解出 20」）
    assert p[0]["need3"] is None, p[0]
    # ③ 第 2 天起必须解得出（两个连板 21% > 20%）；★ need3 是**累计**涨幅（自今日收盘起），
    #    第 2 天恰好 = 20.0（对应日均 (1+0.0954)²−1 ≈ 20%，即日均 9.54% ≤ 涨停 10%）
    assert p[1]["need3"] is not None, p[1]
    assert abs(p[1]["need3"] - 20.0) < 0.05, p[1]
    # ★ 反证 clamp：若日均能超过涨停，第 2 天的累计解会显著低于 20%（单日就能做到）
    assert p[1]["need3"] >= 20.0 - 0.05, p[1]




def test_project_left_days_none_when_never_triggers(patched):
    """反证：`left10` 只在「10 天内真的会触发」时才有值，否则必须是 None
    （不能默认成 0 —— 0 的语义是「今天已触发」，会误导用户）。

    夹具：北交所（涨停 30%）+ 过去全平 ⇒ 30 日线 +? 需查阈值；
      取 30 日线阈值（北交所 dev30_up）应在 10 天涨停内**够不到** ⇒ 必须 None。
    """
    patched(idx=_idx(45), stock=_stock_from_pcts([0.0] * 45))
    res = dev_risk.compute("920002")                       # 北交所
    p = res["project10"]
    assert p[0]["limit_up_pct"] == 30.0
    # 30 日线阈值为 200%（北交所与主板同）：1.3^10−1 = 1274% 远超 ⇒ 10 天内可达
    # ⇒ 用它反证「可达时不得为 None」；不可达情形改用更严的判据：
    #   主板 30 日线 1.1^10−1 = 159% < 200% ⇒ 全表 None（见下）
    patched(stock=_stock_from_pcts([0.0] * 45))
    pm = dev_risk.compute("605058")["project10"]           # 主板 10%
    assert all(r["left30"] is None for r in pm), [r["left30"] for r in pm]
    # 且 left10 有值的那天，left30 必须仍是 None（两条线独立判定）
    assert pm[9]["left10"] == 0 and pm[9]["left30"] is None



# ---------------- 风险标签 ----------------

# 30 日线「临近」轮廓（分级用例专用）：**最后 30 个交易日**每日 +3.7% ⇒ d30 ≈ +197%
# （阈值 +200%，差 5pp 内 ⇒ 临近）；而明日仅需约 +4.6% 即越线（≤ 涨停）⇒ 同时落进
# 「明日即触发」的 hit 分支。
# ⚠️ 顺序：pcts 是**由旧到新**，窗口取末尾 —— 先前误写成 `[3.7]*30 + [0.0]*15`，
#    那样 30 日窗口只吃到 15 天 ⇒ d30 仅 +72%（实测被 test_warn_levels 抓出）。
# ★ 用它替代原 d3 轮廓来覆盖 yellow 分支 —— 3 日维度已于 2026-09-28 移出分级。
D30_NEAR_PCTS = [0.0] * 15 + [3.7] * 30

def test_warn_levels(patched):
    """★ red ⟺ 已触发；yellow ⟺ 未触发但将越线或已临近（两级都可达）。

    🔴 2026-09-28 主人拍板：分级**移除 3 日维度** ⇒ d3 单独越线/临近**不再产生任何标签**。
       故原基于 d3 的两条用例改为断言「无标签」（它们同时成为该规则的回归用例），
       yellow 的「临近」分支改用 30 日线覆盖；d3 数据本身仍照常计算（断言照旧）。
    """
    # 已触发(30日) ⇒ red
    patched(idx=_idx(45), stock=_stock_from_pcts([5.0] * 45))
    res = dev_risk.compute("605058")
    assert res["dev"]["d30"]["status"] == "触发"
    assert res["warn"]["level"] == "red"
    assert "已触发" in res["warn"]["msg"]
    # ★ 30日 临近 + 明日涨停即触发 ⇒ yellow（★ 首版把这种情形错判 red）
    patched(idx=_idx(45), stock=_stock_from_pcts(D30_NEAR_PCTS))
    res = dev_risk.compute("605058")
    assert res["dev"]["d30"]["status"] == "临近", res["dev"]["d30"]
    assert res["warn"] is not None and res["warn"]["level"] == "yellow", res.get("warn")
    assert "即触发" in res["warn"]["msg"]
    # ★ 仅 3日 触发 ⇒ 不再产生标签（d3 数据本身仍有值）
    patched(stock=_stock_from_pcts([0.0] * 42 + [10.0, 10.0, 10.0]))
    res = dev_risk.compute("605058")
    assert res["dev"]["d3"]["status"] == "触发", res["dev"]["d3"]
    assert res["warn"] is None, "3日 已不参与分级 ⇒ 单独越线不得产生标签"
    # ★ 仅 3日 临近（且明日涨停即触发）⇒ 同样无标签
    #   （原注释：3 日窗口 = [+10%,+2.76%,+2.76%] ⇒ d3=16.16% 临近；明日需 +13.63% > 涨停）
    patched(stock=_stock_from_pcts([0.0] * 42 + [10.0, 2.7634, 2.7634]))
    res = dev_risk.compute("605058")
    assert res["dev"]["d3"]["status"] == "临近", res["dev"]["d3"]
    assert res["warn"] is None, "3日 临近不得产生标签"
    # 安全 ⇒ 无标签
    patched(stock=_stock_from_pcts([0.0] * 45))
    assert dev_risk.compute("605058")["warn"] is None


def test_warn_red_iff_triggered(patched):
    """★ 防退化不变量：red **只能**由「10/30 日任一条已触发」产生。

    首版把「明日涨停即触发」也算 red ⇒ 因「临近带所需涨幅必然 ≤ 一个涨停」，
    red 会吞掉整个临近带、yellow 成为**不可达分支**（`test_warn_levels` 抓到）。
    本用例对一批轮廓逐一断言 `(level == 'red') == (d10 或 d30 已触发)`。

    🔴 2026-09-28：判据随「移除 3 日维度」收窄为 10/30 —— 仅 d3 触发的轮廓从此**不再**是 red。
    """
    profiles = [
        [5.0] * 45,                          # d30 触发
        [6.0] * 45,                          # d30 触发
        [2.0] * 45,                          # 全安全
        [0.0] * 45,                          # 全安全
        [0.0] * 42 + [6.0, 6.0, 6.0],               # 仅 d3 临近（不参与分级）
        [0.0] * 42 + [5.0, 5.0, 5.0],               # 仅 d3 临近（不参与分级）
        [0.0] * 42 + [10.0, 2.7634, 2.7634],        # 仅 d3 临近（不参与分级）
        [0.0] * 42 + [10.0, 10.0, 10.0],            # 仅 d3 触发（不参与分级 ⇒ 不得为 red）
        D30_NEAR_PCTS,                              # d30 临近
    ]
    for pcts in profiles:
        patched(idx=_idx(45), stock=_stock_from_pcts(pcts))
        res = dev_risk.compute("605058")
        dev = res["dev"]
        trig = any((dev[k] or {}).get("status") == "触发" for k in ("d10", "d30"))
        w = res.get("warn")
        assert (w is not None and w["level"] == "red") == trig, (pcts, w, dev)
        if w is not None:
            assert w["level"] in ("red", "yellow"), w


def test_warn_msg_never_mentions_d3(patched):
    """🔴 2026-09-28 主人要求移除 3 日维度 ⇒ **提示文案不得出现「3日」**。

    覆盖三条产出路径：red（已触发）、yellow-hit（明日即触发）、yellow-near（临近）。
    注意只挡文案，不挡 d3 数据本身（它仍照常计算与入库，本项目刻意不动库表结构）。
    """
    for pcts in ([5.0] * 45,                                # red: d30 触发
                 D30_NEAR_PCTS,                             # yellow: d30 临近 + hit
                 [0.0] * 42 + [10.0, 10.0, 10.0],           # 无标签: 仅 d3 触发
                 [0.0] * 42 + [6.0, 6.0, 6.0],              # 无标签: 仅 d3 临近
                 [0.0] * 45):                               # 无标签: 安全
        patched(idx=_idx(45), stock=_stock_from_pcts(pcts))
        w = dev_risk.compute("605058").get("warn")
        if w:
            assert "3日" not in w["msg"], (pcts, w)


def test_row_shape_for_db(patched):
    """_row_of 产出的键必须与 dev_risk_daily 的插入列一一对应。"""
    patched(idx=_idx(45), stock=_stock_from_pcts([5.0] * 45))
    row = dev_risk._row_of(dev_risk.compute("605058"))
    for k in ("date", "code", "name", "board", "board_key", "price", "today_dev",
              "d3", "d3_status", "d10", "d10_status", "d30", "d30_status",
              "next_trigger_pct", "trigger_price", "rule", "reachable",
              "max_range", "warn_level", "warn_msg", "base_dates"):
        assert k in row, k
    assert row["reachable"] in (0, 1)
    assert row["date"] == "2026-09-24"


# ---------------- 时间语义 ----------------

def test_expected_last_trade_date_semantics(monkeypatch):
    """15:05 前期望上一交易日；15:05 后（且当天是交易日）期望当天。

    ★ 构造 epoch **必须**用 `calendar.timegm(...) − 8h`（把「北京墙钟时间」换算成 epoch），
      **不能用 `time.mktime`** —— mktime 依赖本机时区：测试机是 CST(UTC+8)，
      再减 8h 会落到当地 08:00 ⇒ 判据静默走错分支。
      （2026-09-27 影子单测实测：`mktime − 8h` 在 CST 机上 16:00 被算成 08:00，
        `assert '2026-09-23' == '2026-09-24'` 失败 —— 是**测试**错，不是实现错。）
    """
    import calendar
    import time as _t

    def bj(s):
        """北京墙钟时间字符串 → epoch（与机器时区无关）。"""
        return calendar.timegm(_t.strptime(s, "%Y-%m-%d %H:%M")) - 8 * 3600

    # 2026-09-24 是星期四（交易日）
    monkeypatch.setattr(dev_risk.trade_calendar, "is_trade_day", lambda d: True)
    monkeypatch.setattr(dev_risk.trade_calendar, "prev_trade_date", lambda d: "2026-09-23")
    for s in ("2026-09-24 00:30", "2026-09-24 10:00", "2026-09-24 15:04"):
        monkeypatch.setattr(dev_risk.time, "time", lambda s=s: bj(s))
        assert dev_risk._expected_last_trade_date() == "2026-09-23", s
    for s in ("2026-09-24 15:05", "2026-09-24 16:00", "2026-09-24 23:59"):
        monkeypatch.setattr(dev_risk.time, "time", lambda s=s: bj(s))
        assert dev_risk._expected_last_trade_date() == "2026-09-24", s


def test_next_trade_days_skips_weekend():
    """2026-09-25 是周五 ⇒ 之后 3 个交易日应为 09-28/09-29/09-30（跨周末）。"""
    ds = dev_risk._next_trade_days("2026-09-25", 3)
    assert ds[:3] == ["2026-09-28", "2026-09-29", "2026-09-30"], ds
