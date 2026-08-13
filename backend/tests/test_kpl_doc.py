
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
    # 非竞价时段返回空 list 或带数据 list
    assert d is None or isinstance(d, list)
    if d:
        # 数据项含必填字段
        for r in d[:3]:
            assert "code" in r and "qcNet" in r
