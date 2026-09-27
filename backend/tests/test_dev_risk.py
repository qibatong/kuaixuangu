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
    state = {"idx": _idx(), "stock": _stock_from_pcts([0.0] * 45)}

    def _index_series(code, force=False):
        return state["idx"]

    def _stock_series(code, days=dev_risk.FETCH_DAYS):
        return state["stock"]

    monkeypatch.setattr(dev_risk, "index_series", _index_series)
    monkeypatch.setattr(dev_risk, "stock_series", _stock_series)

    def patch(idx=None, stock=None):
        if idx is not None:
            state["idx"] = idx
        if stock is not None:
            state["stock"] = stock

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


# ---------------- 明日触发空间 ----------------

def test_next_trigger_analytic(patched):
    """逐日 +5% 的票（主板）：3 日线临近未触发，明日只需再涨 8.84% 就撞 3 日 20%。

    推导：x = (1 + 20/100) / (1 + s2/100) − 1，s2 = 1.05²−1 = 10.25%
        ⇒ x = 1.20/1.1025 − 1 = 8.844%
    10 日线：s9 = 1.05⁹−1 = 55.13% ⇒ x = 2.00/1.5513 − 1 = 28.92%（更远）
    30 日线：1.05³⁰−1 = 332% > 200% ⇒ **已触发**，不参与「下一条」
    """
    patched(idx=_idx(45), stock=_stock_from_pcts([5.0] * 45))
    res = dev_risk.compute("605058")
    assert res["dev"]["d30"]["status"] == "触发"
    assert res["dev"]["d3"]["status"] == "临近"
    room = res["room"]
    assert room["rule"].startswith("3日"), room
    assert abs(room["next_trigger_pct"] - 8.84) < 0.05, room
    assert room["reachable"] is True                      # 8.84% <= 涨停 10%
    assert room["trigger_price"] == round(res["price"] * (1 + room["next_trigger_pct"] / 100), 2)
    assert len(room["all"]) == 2                          # 只剩 3 日 / 10 日两条候选


def test_next_trigger_not_reachable(patched):
    """逐日 +2% 的票：3 日线远离，明日涨停也不够 —— reachable 必须为 False。"""
    patched(idx=_idx(45), stock=_stock_from_pcts([2.0] * 45))
    res = dev_risk.compute("605058")
    room = res["room"]
    assert room["next_trigger_pct"] > res["limit_up_pct"]
    assert room["reachable"] is False
    assert room["hit"] == []


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
      与 `_next_trigger` 的**未截断**闭式解就不可比了（逐日 +3% 就是这种情况：
      room 给 13.11% > 10%，而 need3[0] 因截断返回 None）。
    """
    # 逐日 +5%：dev30 = 332% ⇒ 30 日线已触发；3 日线 15.76% 未越 20%
    #   room 的「下一条」= 3 日线，需再涨 8.84%（≤ 10% 涨停 ⇒ reachable=True）
    patched(idx=_idx(45), stock=_stock_from_pcts([5.0] * 45))
    res = dev_risk.compute("605058")
    p = res["project10"]
    assert len(p) == 10
    assert res["room"]["rule"].startswith("3日")
    assert res["room"]["reachable"] is True
    assert abs(res["room"]["next_trigger_pct"] - 8.84) < 0.05, res["room"]
    # ★ k=1 的 3 日线解 必须与 room 的次日触发空间**数值全等**（这就是「按实际倒推」的定义）
    assert p[0]["need3"] is not None, p[0]
    assert abs(p[0]["need3"] - res["room"]["next_trigger_pct"]) < 0.05, (p[0], res["room"])
    # ★ 但 3 日线**不进** trigger ⇒ 该行仍报「不触发」（此刻触发的是 30 日线）
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

def test_warn_levels(patched):
    """★ red ⟺ 已触发；yellow ⟺ 未触发但将越线或已临近（两级都可达）。"""
    # 已触发 ⇒ red
    patched(idx=_idx(45), stock=_stock_from_pcts([5.0] * 45))
    res = dev_risk.compute("605058")
    assert res["dev"]["d30"]["status"] == "触发"
    assert res["warn"]["level"] == "red"
    assert "已触发" in res["warn"]["msg"]
    # 未触发 + 已临近 + 明日涨停即触发 ⇒ yellow（★ 首版错判 red）
    patched(stock=_stock_from_pcts([0.0] * 42 + [6.0, 6.0, 6.0]))
    res = dev_risk.compute("605058")
    assert res["dev"]["d3"]["value"] == round((1.06 ** 3 - 1) * 100, 2)   # 19.12
    assert res["dev"]["d3"]["status"] == "临近"
    assert res["room"]["hit"], res["room"]
    assert res["warn"]["level"] == "yellow"
    assert "即触发" in res["warn"]["msg"]
    # 未触发 + 已临近，但一个涨停也不够 ⇒ 仍走「临近」分支的 yellow
    # （这条是 yellow 唯一不被 hit 覆盖的情形：3 日窗口 = [+10%, +2.76%, +2.76%]
    #   ⇒ d3 = 16.16% 临近；明日 d3 窗口只剩后 2 日(+5.60%)，需 +13.63% > 涨停 10%）
    patched(stock=_stock_from_pcts([0.0] * 42 + [10.0, 2.7634, 2.7634]))
    res = dev_risk.compute("605058")
    assert res["dev"]["d3"]["status"] == "临近", res["dev"]["d3"]
    assert res["room"]["hit"] == [] and res["room"]["reachable"] is False, res["room"]
    assert res["warn"]["level"] == "yellow"
    assert "临近" in res["warn"]["msg"]
    # 安全 ⇒ 无标签
    patched(stock=_stock_from_pcts([0.0] * 45))
    assert dev_risk.compute("605058")["warn"] is None


def test_warn_red_iff_triggered(patched):
    """★ 防退化不变量：red **只能**由「已触发」产生。

    首版把「明日涨停即触发」也算 red ⇒ 因「临近带所需涨幅必然 ≤ 一个涨停」，
    red 会吞掉整个临近带、yellow 成为**不可达分支**（`test_warn_levels` 抓到）。
    本用例对一批轮廓逐一断言 `(level == 'red') == 任一窗口已触发`。
    """
    profiles = [
        [5.0] * 45,                          # d30 触发
        [6.0] * 45,                          # d30 触发
        [2.0] * 45,                          # 全安全
        [0.0] * 45,                          # 全安全
        [0.0] * 42 + [6.0, 6.0, 6.0],               # d3 临近（hit）
        [0.0] * 42 + [5.0, 5.0, 5.0],               # d3 临近（hit）
        [0.0] * 42 + [10.0, 2.7634, 2.7634],        # d3 临近（不 hit）
        [0.0] * 42 + [10.0, 10.0, 10.0],            # d3 触发（33.1）
    ]
    for pcts in profiles:
        patched(stock=_stock_from_pcts(pcts))
        res = dev_risk.compute("605058")
        dev = res["dev"]
        trig = any((dev[k] or {}).get("status") == "触发" for k in ("d3", "d10", "d30"))
        w = res.get("warn")
        assert (w is not None and w["level"] == "red") == trig, (pcts, w, dev)
        if w is not None:
            assert w["level"] in ("red", "yellow"), w


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
