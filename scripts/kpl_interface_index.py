# -*- coding: utf-8 -*-
"""
开盘啦接口索引生成器
=====================================================================
扫描 backend/app/services/kpl.py 源码, 提取所有已封装接口的元信息,
生成 docs/kpl-interfaces.md 索引文档。

用法:
  python scripts/kpl_interface_index.py

输出:
  docs/kpl-interfaces.md  (按功能分类的接口清单)

提取内容:
  - fetch_kpl_docXX: 编号接口(开盘啦官方文档 docXX)
    → docstring 第一行功能名 / host / a= / c= / apiv= / 是否已被业务调用
  - fetch_xxx: 具名业务封装(带语义名)
    → 函数签名 / docstring 第一行 / 是否被其他模块引用

判断"是否已接入业务":
  统计源码中 `fetch_xxx(` 出现次数, > 定义处 1 次 即视为被调用过。
"""
import ast
import os
import re

# 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KPL_SRC = os.path.join(ROOT, "backend", "app", "services", "kpl.py")
OUT_DOC = os.path.join(ROOT, "docs", "kpl-interfaces.md")

# doc 编号 → 功能分类 (按开盘啦官方文档语义, 缺失的自动归入"其他")
DOC_CATEGORY = {
    7: "个股行情-日K", 8: "个股行情-分时", 9: "个股行情-盘口", 13: "个股行情", 14: "个股行情",
    15: "个股行情", 16: "个股行情", 17: "个股行情", 18: "个股行情", 19: "个股行情", 20: "个股行情",
    21: "个股行情", 22: "个股行情", 23: "个股行情", 24: "个股行情",
    30: "竞价-涨停委买额(历史)", 31: "竞价", 33: "竞价",
    41: "竞价", 42: "竞价", 43: "竞价",
    46: "竞价", 47: "竞价", 48: "竞价", 49: "竞价", 50: "竞价", 51: "竞价", 52: "竞价",
    53: "竞价", 54: "竞价", 55: "竞价", 56: "竞价", 57: "竞价", 58: "竞价", 59: "竞价",
    60: "竞价", 61: "竞价", 62: "竞价", 63: "竞价", 64: "竞价", 65: "竞价", 66: "竞价",
    67: "竞价", 68: "竞价", 69: "竞价",
    70: "盘中-短线精灵", 71: "盘中-人气热榜", 72: "指数-全球指数",
    74: "盘中", 76: "盘中", 77: "盘中", 78: "盘中", 79: "盘中", 80: "盘中", 81: "盘中",
    82: "盘中", 83: "盘中", 84: "盘中", 85: "盘中", 86: "盘中", 87: "盘中", 88: "盘中",
    89: "盘中", 90: "盘中", 91: "盘中", 92: "盘中", 93: "盘中",
    94: "个股-全部相关概念板块", 95: "资讯-头条", 96: "资讯-新闻", 97: "资讯", 98: "资讯",
    99: "资讯", 100: "资讯", 101: "资讯",
    103: "个股", 104: "个股", 105: "个股", 106: "个股", 107: "个股", 108: "个股",
    109: "个股", 110: "个股", 111: "个股", 112: "个股", 113: "个股", 115: "竞价-涨停委买额(实时)",
}

# 具名业务接口 → 分类 (按函数名推断)
NAMED_CATEGORY = {
    "fetch_bid_seal": "竞价-涨停委买额(实时)",
    "fetch_bid_boom": "竞价-爆量榜",
    "fetch_sentiment": "市场情绪",
    "fetch_ladder": "连板梯队",
    "fetch_ladder_all": "连板梯队",
    "fetch_zt_reason": "涨停原因",
    "fetch_board_rank": "板块强度",
    "fetch_board_rank_by_date": "板块强度(历史)",
    "fetch_wpqc": "尾盘抢筹",
    "fetch_hot_rank": "人气榜",
    "fetch_lhb": "龙虎榜",
    "fetch_lhb_detail": "龙虎榜-明细",
    "fetch_yesterday_perf": "昨日涨停表现",
    "fetch_zt_pool": "涨停池",
    "fetch_dt_pool": "跌停池",
    "fetch_yest_zt_pool": "昨日涨停池",
    "fetch_updown_line": "涨跌家数曲线",
    "fetch_zt_dt_line": "涨停跌停曲线",
    "fetch_broken_line": "炸板率曲线",
    "fetch_yest_zt_perf_line": "昨涨停今表现曲线",
    "fetch_market_temp_line": "市场温度曲线",
    "fetch_hot_stocks": "热点解读-强势股",
    "fetch_hot_plates": "热点解读-板块",
    "fetch_live_room": "直播",
    "fetch_dadan_net": "大单净额",
    "fetch_broken_zt": "炸板股",
    "fetch_board_map": "概念合并(内部)",
    "fetch_yest_zt": "昨日涨停今表现(内部)",
    "fetch_yest_broken": "昨日炸板(内部)",
    "fetch_bid_qiangcang": "竞价抢筹",
    "fetch_stock_plate": "个股-全部相关概念板块",
}


def extract_docstring_meta(node):
    """从函数节点提取 (title, host, a, c, apiv)"""
    doc = ast.get_docstring(node) or ""
    first = doc.strip().splitlines()[0] if doc.strip() else ""
    # host: 兼容 "(apphis.longhuvip.com)" / "(apphwhq/apphwshhq.longhuvip.com)" / "(default)"
    m_host = re.search(r"\(([^)]*longhuvip\.com[^)]*|default)\)", first)
    host = m_host.group(1) if m_host else ""
    # 从 docstring 全文找 a=/c=/apiv=
    m_a = re.search(r"a=([A-Za-z0-9_]+)", doc)
    m_c = re.search(r"c=([A-Za-z0-9_]+)", doc)
    m_apiv = re.search(r"apiv=(w\d+)", doc)
    return (
        first.strip(),
        host,
        m_a.group(1) if m_a else "",
        m_c.group(1) if m_c else "",
        m_apiv.group(1) if m_apiv else "",
    )


def count_calls(src, func_name):
    """统计函数名在源码中出现次数(定义处 1 次; >1 视为被调用)"""
    return src.count(func_name + "(")


def count_calls_all(func_name):
    """AST 精确统计: 全 backend 目录中该函数名被调用的次数(排除函数自身定义/递归)
    比字符串 count 更准: docstring/注释里的提及不会算作调用"""
    n = 0
    for root, dirs, files in os.walk(os.path.join(ROOT, "backend")):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache", "venv", ".venv")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=p)
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                # 普通调用 name(...) 或 kpl.name(...)
                if isinstance(f, ast.Name) and f.id == func_name:
                    n += 1
                elif isinstance(f, ast.Attribute) and f.attr == func_name:
                    n += 1
    return n


def main():
    with open(KPL_SRC, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)

    doc_interfaces = []    # (编号, title, host, a, c, apiv, called)
    named_interfaces = []  # (name, title, called)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        title, host, a, c, apiv = extract_docstring_meta(node)
        m = re.match(r"^fetch_kpl_doc(\d+)$", name)
        if m:
            num = int(m.group(1))
            called = count_calls_all(name) > 0
            doc_interfaces.append((num, title, host, a, c, apiv, called))
        elif name.startswith("fetch_") and not name.startswith("fetch_kpl_doc"):
            called = count_calls_all(name) > 0
            named_interfaces.append((name, title, called))

    # 排序
    doc_interfaces.sort(key=lambda x: x[0])
    named_interfaces.sort(key=lambda x: x[0])

    lines = []
    lines.append("# 开盘啦 (KPL) 已封装接口索引\n")
    lines.append("> 自动生成: `python scripts/kpl_interface_index.py` — 请勿手改。\n")
    lines.append("> 数据源服务: `backend/app/services/kpl.py`\n")
    lines.append("> 调用方式: 统一走 `_call(host_key, params)` (POST form-urlencoded + Token/UserID/DeviceID 注入), 新增接口只需写 `fetch_kpl_docXX` + `_cached` 缓存。\n")
    lines.append("\n## 一、编号接口 (fetch_kpl_docXX, 共 %d 个)\n" % len(doc_interfaces))
    lines.append("| 编号 | 功能 | host | a= | c= | apiv | 已接入业务 |")
    lines.append("|------|------|------|----|----|------|:---:|")
    for num, title, host, a, c, apiv, called in doc_interfaces:
        cat = DOC_CATEGORY.get(num, "其他")
        title_full = "**%s** — %s" % (cat, title) if title else "**%s**" % cat
        lines.append("| doc%d | %s | %s | `%s` | `%s` | %s | %s |" % (
            num, title_full, host or "-", a or "-", c or "-", apiv or "-",
            "✅" if called else "⬜ 未接入"))

    lines.append("\n## 二、具名业务封装 (fetch_xxx, 共 %d 个)\n" % len(named_interfaces))
    lines.append("| 函数 | 功能 | 已接入业务 |")
    lines.append("|------|------|:---:|")
    for name, title, called in named_interfaces:
        cat = NAMED_CATEGORY.get(name, "其他")
        title_full = "**%s** — %s" % (cat, title) if title else "**%s**" % cat
        lines.append("| `%s` | %s | %s |" % (name, title_full, "✅" if called else "⬜ 未接入"))

    lines.append("\n---\n")
    lines.append("## 附: 其他数据源封装\n")
    lines.append("- `backend/app/services/fetcher.py` — 东财行情/日K/竞价 (ensure_cache/ensure_spot_cache/fetch_yesterday_amounts 等)\n")
    lines.append("- `backend/app/services/sector_rotation.py` — 板块轮动 (kpl/em 双源)\n")
    lines.append("- `backend/app/services/hot_rank.py` — 人气榜 (kpl/em/ths 三源)\n")

    os.makedirs(os.path.dirname(OUT_DOC), exist_ok=True)
    with open(OUT_DOC, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("生成完成: %s" % OUT_DOC)
    print("  编号接口: %d 个, 具名接口: %d 个" % (len(doc_interfaces), len(named_interfaces)))
    print("  已接入: %d, 未接入: %d" % (
        sum(1 for _, _, _, _, _, _, c in doc_interfaces if c),
        sum(1 for _, _, _, _, _, _, c in doc_interfaces if not c)))


if __name__ == "__main__":
    main()
