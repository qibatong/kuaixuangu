"""fetch_raw_by_codes 点查字段防回归(2026-09-07 生产事故):
曾只列 15 字段漏 f615(竞价涨幅)/f17/f630 → scorer.get_bid_change 退 f3(现价涨幅)
→ 盘后快照候选 filter 产出「竞涨=现涨」+ 大跌票混入。fields 必须整段 = config.FIELDS。"""
import io
import json
import os
import sys

os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services import fetcher, scorer
from app.core import config

# conftest 的 session fixture 会把 fetcher.fetch_raw_by_codes 整体桩掉(防测试打真实网络),
# 但**本文件测的就是这个函数本身** → 必须在用例内还原真实实现。
# 模块导入早于 session fixture, 此刻拿到的是未被桩的原始函数。
_REAL_BY_CODES = fetcher.fetch_raw_by_codes


@pytest.fixture(autouse=True)
def _restore_real_by_codes(monkeypatch):
    """还原真实 fetch_raw_by_codes; 用例内若自行 monkeypatch 则以其为准"""
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", _REAL_BY_CODES)


class _JsonResp:
    """模拟 urlopen 返回值: 支持 with + read()"""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a, **k):
        return io.BytesIO(self._body).read()


def _fake_ulist(payload):
    """生成 fake urlopen: 记录最后一个请求 URL, 返回 payload"""
    state = {"url": None}

    def fake_urlopen(req, timeout=5, context=None):
        state["url"] = req.full_url or req.get_full_url()
        return _JsonResp(payload)

    return fake_urlopen, state


def test_fields_use_full_config_fields(monkeypatch):
    """核心防回归: 点查请求 fields 必须 = config.FIELDS(含 f615/f17/f630),
    不得手写子集漏竞价字段"""
    fake, state = _fake_ulist({"rc": 0, "data": {"diff": [
        {"f12": "600001", "f14": "测试A", "f615": 3.5, "f3": -6.2}]}})
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", fake)
    monkeypatch.setattr(fetcher, "_broken_hosts", {})
    out = fetcher.fetch_raw_by_codes(["600001"])
    assert len(out) == 1
    # 请求必须携带完整 FIELDS
    assert "f615" in state["url"] and "fields" in state["url"]
    for fld in ("f615", "f17", "f630", "f616", "f617", "f618"):
        assert fld in state["url"], "点查 fields 缺 %s → bidChange 退 f3 事故" % fld


def test_bid_change_uses_f615_not_f3(monkeypatch):
    """语义验证: 点查行喂 scorer, get_bid_change 必须取 f615(竞价涨幅),
    而非 f3(现价/收盘涨幅) — 直接复现「现涨=竞涨/大跌票混入」事故路径"""
    # 竞价涨幅 +3.5%, 但当日大跌 -6.2%(现涨) → bidChange 必须 3.5
    fake, _ = _fake_ulist({"rc": 0, "data": {"diff": [
        {"f12": "600001", "f14": "测试A",
         "f2": 9.38, "f3": -6.2, "f615": 3.5, "f616": 3000000.0}]}})
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", fake)
    monkeypatch.setattr(fetcher, "_broken_hosts", {})
    rows = fetcher.fetch_raw_by_codes(["600001"])
    assert rows, "点查应返回行"
    bid_chg = scorer.get_bid_change(rows[0])
    real_chg = scorer.parse_float(rows[0].get("f3"))
    assert bid_chg == 3.5, "bidChange 应取 f615=3.5, 实际 %s(退 f3 事故)" % bid_chg
    assert real_chg == -6.2
    assert bid_chg != real_chg, "现涨与竞涨不得相等"


def test_empty_list_returns_empty():
    assert fetcher.fetch_raw_by_codes([]) == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
