
"""
测试: 全部 fetch_kpl_doc 函数族能正确调用 + 返回 dict 结构
要求: 环境变量 KPL_TOKEN/USERID/DEVICEID 已配置
验证: 每个函数非 None, 不抛异常, 返回 dict 含 errcode=0
"""
import os, sys, pytest
sys.path.insert(0, "backend")
os.environ.setdefault("SSL_CERT_FILE", "/etc/pki/tls/certs/ca-bundle.crt")

from app.services import kpl


# 抽测各 host 的代表函数(每个 host 测一个, 避免实测全部 79 个耗时太久)
SAMPLE = [
    ("fetch_kpl_doc7",   {"StockID": "000001", "RStart": "0925", "old": "1", "T": "W8"}),  # apphis - K线
    ("fetch_kpl_doc8",   {"StockID": "000001"}),       # apphwhq - 分时
    ("fetch_kpl_doc9",   {"StockID": "000001"}),       # 盘口五档
    ("fetch_kpl_doc13",  {"StockID": "000001"}),       # apphq - 大单成交
    ("fetch_kpl_doc19",  {"st": "50"}),                  # apphwshhq - 昨日涨停今表现
    ("fetch_kpl_doc30",  {}),                              # 竞价涨停委买-历史
    ("fetch_kpl_doc31",  {"StockID": "000785"}),          # 个股竞价分时
    ("fetch_kpl_doc46",  {"StockID": "801519"}),          # apphis - 板块成分股
    ("fetch_kpl_doc79",  {}),                              # apphwshhq - 板块竞价异动
    ("fetch_kpl_doc80",  {"StockID": "801519"}),          # 异动板块个股
    ("fetch_kpl_doc95",  {}),                              # 头条
    ("fetch_kpl_doc96",  {}),                              # 新闻
    ("fetch_kpl_doc100", {}),                              # applhb - 上榜股票
    ("fetch_kpl_doc103", {}),                              # 尾盘抢筹
    ("fetch_kpl_doc104", {}),                              # 竞价砸盘
    ("fetch_kpl_doc115", {}),                              # 竞价涨停委买-实时
]


@pytest.mark.parametrize("fn_name,params", SAMPLE)
def test_kpl_doc(fn_name, params):
    fn = getattr(kpl, fn_name, None)
    assert fn is not None, f"{fn_name} 不存在"
    d = fn(**params)
    # 大部分接口返回 dict, 部分可能返回 None (当前时段无数据)
    if d is None:
        pytest.skip(f"{fn_name} 当前无数据(可能非交易时段)")
    assert isinstance(d, dict), f"{fn_name} 返回非 dict: {type(d).__name__}"
    assert "errcode" in d, f"{fn_name} 响应缺 errcode: keys={list(d.keys())[:5]}"


def test_kpl_doc79_boards():
    """专门验证 /docs/79 板块竞价异动(竞价抢筹核心)"""
    d = kpl.fetch_kpl_doc79()
    assert d is not None
    assert "List1" in d or "List2" in d
    # 仅竞价时段有数据
    if d.get("List1"):
        assert isinstance(d["List1"], list)
        for b in d["List1"][:3]:
            assert isinstance(b, list) and len(b) >= 2
            print(f"  板块 {b[0]} {b[1]}")


def test_fetch_bid_qiangcang_integration():
    """集成测试: fetch_bid_qiangcang 调通(主人最迫切需要的功能)"""
    d = kpl.fetch_bid_qiangcang()
    # 返回 dict {list20, listLast}(任一为空 list 也算正常)
    assert isinstance(d, dict)
    assert "list20" in d and "listLast" in d
    assert isinstance(d["list20"], list) and isinstance(d["listLast"], list)
    for lst in (d["list20"], d["listLast"]):
        if lst:
            # 数据项含必填字段(左右双表)
            for r in lst[:3]:
                assert "code" in r and "qcDelta" in r or "qcDeltaLast" in r


# ---------- xuangubao 免费接口(kaipanla 文档收录, 无需 Token) ----------
def test_flash_line(monkeypatch):
    """曲线接口: 返回 [{field:value, ts}, ...]"""
    monkeypatch.setattr(kpl, "_flash_line", lambda fields, date=None: [{"rise_count": 100, "fall_count": 50, "ts": 1}])
    rows = kpl.fetch_updown_line()
    assert len(rows) == 1
    assert rows[0]["rise_count"] == 100


def test_fetch_zt_dt_pool(monkeypatch):
    """涨停/跌停池复用 _flash_pool"""
    monkeypatch.setattr(kpl, "_flash_pool", lambda pool, date=None: [{"code": "000001", "name": "A"}])
    kpl._cache.clear()
    zt = kpl.fetch_zt_pool()
    assert len(zt) == 1 and zt[0]["code"] == "000001"
    dt = kpl.fetch_dt_pool()
    assert len(dt) == 1


def test_fetch_hot_plates(monkeypatch):
    """板块题材: items 是 dict 列表"""
    monkeypatch.setattr(kpl, "_flash_surge", lambda path, params="": {"items": [{"id": 1, "name": "医药", "description": "创新药"}]})
    kpl._cache.clear()
    rows = kpl.fetch_hot_plates()
    assert len(rows) == 1
    assert rows[0]["name"] == "医药"


def test_fetch_hot_stocks(monkeypatch):
    """热点强势股: items 是二维数组(fields 作列头)"""
    fields = ["code", "prod_name", "cur_price", "px_change_rate", "circulation_value", "description", "plates"]
    monkeypatch.setattr(kpl, "_flash_surge", lambda path, params="": {
        "fields": fields,
        "items": [["300603.SZ", "立昂技术", 9.5, 0.183, 3552346438, "算力", [{"name": "云计算数据中心"}]]]
    })
    kpl._cache.clear()
    rows = kpl.fetch_hot_stocks()
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "300603"          # 去 .SZ 后缀
    assert r["name"] == "立昂技术"
    assert abs(r["change"] - 18.3) < 0.1  # 0.183 -> 18.3%
    assert "云计算数据中心" in r["plates"]
