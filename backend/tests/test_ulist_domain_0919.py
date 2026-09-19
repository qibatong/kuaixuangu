"""东财 ulist 点查域名修复（2026-09-19, v4.11.33）回归用例。

事故现实
--------
`fetcher._ULIST_URL` 原**写死** `https://push2.eastmoney.com/api/qt/ulist.np/get`。
2026-09-19 实测（用测试机已部署的 fetcher 本体, 只读）证伪了"接口级封死"的旧结论:

    https://push2.eastmoney.com/api/qt/ulist.np/get       → RemoteDisconnected(48ms 秒断)
    https://push2dycalc.eastmoney.com/api/qt/ulist.np/get → rc=0 正常返回, **且带 f630**

⇒ **封的是域名, 不是接口**。走写死域名的 `fetch_raw_by_codes` 因此**必然失败**:
      · `picker` 的 `eastmoney_realtime` 补丁源形同虚设
      · 每轮选股降级腾讯点查, 而**腾讯无 f630** → 17% 异动权重退化
      · 生产日志长期刷「选股快照候选池东财点查失败→腾讯点查兜底成功」

修复
----
改为 `_ULIST_HOSTS`（push2dycalc 首选 / push2 备用）**顺序重试** + `_broken_hosts`
域名冷却 300s（复用 `config.KLINE_HOSTS` 的既有范式）。准则:
  · 连接层异常 → 标记坏域名, 换下一个
  · `rc != 0` 是**数据层**错误(域名通的) → 不标记, 只换域名重试
  · 全部域名在冷却中 → 仍尝试(fail-open, 否则上游恢复后永久哑火)

本文件守住下面四条, 任何一条红都意味着"补丁源可能再次静默失效":
  ① 首选域名必须是 push2dycalc（不得回退到写死 push2）
  ② 健康时只打首选域名一次（不做无意义的多打）
  ③ 首选域名连接失败时必须换域名并拿到数据（不是直接失败）
  ④ 坏域名必须进冷却, 且后续调用不再反复打它
  ⑤ 全部域名在冷却中时仍要尝试（fail-open, 不能被冷却永久锁死）
"""
import io
import inspect
import json
import os
import sys
import urllib.parse

os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services import fetcher
from app.core import config

# conftest 的 session fixture 会把 fetcher.fetch_raw_by_codes 整体桩掉(防测试打真实网络),
# 但**本文件测的就是这个函数本身** → 用例内必须还原真实实现(与 test_fetch_raw_by_codes 同法)。
_REAL_BY_CODES = fetcher.fetch_raw_by_codes

_PRIMARY = "https://push2dycalc.eastmoney.com"
_BACKUP = "https://push2.eastmoney.com"
_DEAD_FULL_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """还原真实实现 + 清空坏域名表（防跨用例顺序耦合）"""
    monkeypatch.setattr(fetcher, "fetch_raw_by_codes", _REAL_BY_CODES)
    monkeypatch.setattr(fetcher, "_broken_hosts", {})


class _JsonResp:
    """最小 urlopen 返回值: with + read()"""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a, **k):
        return io.BytesIO(self._body).read()


def _install(monkeypatch, handler):
    """handler(host) -> payload(dict) 或 Exception; 返回被请求的 host 顺序列表"""
    calls = []

    def fake_urlopen(req, timeout=5, context=None):
        url = req.full_url or req.get_full_url()
        u = urllib.parse.urlparse(url)
        host = "%s://%s" % (u.scheme, u.netloc)
        calls.append((host, u.path))
        r = handler(host)
        if isinstance(r, Exception):
            raise r
        return _JsonResp(r)

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", fake_urlopen)
    return calls


def _ok(diff=None):
    return {"rc": 0, "data": {"diff": diff if diff is not None else [
        {"f12": "600001", "f14": "测试A", "f615": 3.5, "f630": 4}]}}


def _codes(n):
    return ["6000%02d" % i for i in range(n)]


# ---------------------------------------------------------------- ① 域名本身

def test_primary_host_is_push2dycalc_not_push2():
    """核心防回归: 首选域名必须是 push2dycalc —— 原写死 push2 使补丁源必然失败"""
    assert fetcher._ULIST_HOSTS[0] == _PRIMARY, (
        "首选域名必须是与 clist 同域名的 push2dycalc; "
        "若退回 push2 则补丁源重新变为「必然失败」")
    assert _BACKUP in fetcher._ULIST_HOSTS, "备用域名应保留, 以便上游切换时兜底"


def test_all_hosts_use_same_ulist_path():
    """多域名重试的前提: 只换域名、不换接口路径(实测同 path 换域名即通)"""
    assert fetcher._ULIST_PATH == "/api/qt/ulist.np/get"


def test_no_hardcoded_dead_domain_constant():
    """防回归: 不得再出现「写死被封域名」的模块级 URL 常量"""
    for name in dir(fetcher):
        if name.startswith("__"):
            continue
        v = getattr(fetcher, name, None)
        if isinstance(v, str) and v == _DEAD_FULL_URL:
            raise AssertionError("发现写死被封域名的常量 fetcher.%s = %r" % (name, v))
    # 主机必须来自 _ULIST_HOSTS, 不得在函数体内写死域名
    src = inspect.getsource(fetcher._fetch_ulist_batch)
    assert "_ULIST_HOSTS" in src
    assert _DEAD_FULL_URL not in src and "push2dycalc" not in src


# ---------------------------------------------------------------- ② 健康路径

def test_healthy_path_hits_primary_host_only_once(monkeypatch):
    calls = _install(monkeypatch, lambda host: _ok())
    out = fetcher.fetch_raw_by_codes(["600001"])
    assert len(out) == 1 and out[0]["f12"] == "600001"
    assert [c[0] for c in calls] == [_PRIMARY], "健康时不得打备用域名(无意义多打)"
    assert calls[0][1] == fetcher._ULIST_PATH


# ---------------------------------------------------------------- ③ 换域名

def test_connection_error_switches_to_backup_host(monkeypatch):
    """首选域名 RemoteDisconnected → 必须换域名并拿到数据(这是本次修复的核心)"""

    def handler(host):
        if host == _PRIMARY:
            raise ConnectionResetError("RemoteDisconnected(模拟 push2 整站 RST)")
        return _ok([{"f12": "600002", "f14": "测试B", "f630": 3}])

    calls = _install(monkeypatch, handler)
    out = fetcher.fetch_raw_by_codes(["600002"])
    assert len(out) == 1 and out[0]["f630"] == 3, "换域名后应正常拿到数据(含 f630)"
    assert [c[0] for c in calls] == [_PRIMARY, _BACKUP], "应先试首选再试备用"


def test_all_hosts_connection_error_raises(monkeypatch):
    _install(monkeypatch, lambda host: ConnectionResetError("全挂"))
    with pytest.raises(RuntimeError) as ei:
        fetcher.fetch_raw_by_codes(["600001"])
    assert "已试域名" in str(ei.value)


def test_connection_failure_marks_host_broken(monkeypatch):
    """坏域名必须进 _broken_hosts 冷却(避免下次又白打一次)"""
    _install(monkeypatch, lambda host: ConnectionResetError("挂") if host == _PRIMARY else _ok())
    fetcher.fetch_raw_by_codes(["600001"])
    assert _PRIMARY in fetcher._broken_hosts
    assert fetcher._host_blocked(_PRIMARY) is True
    assert not fetcher._host_blocked(_BACKUP), "成功的域名不得被标记"


def test_broken_host_skipped_on_next_call(monkeypatch):
    """第二次调用应直接跳过冷却中的域名"""
    _install(monkeypatch, lambda host: ConnectionResetError("挂") if host == _PRIMARY else _ok())
    fetcher.fetch_raw_by_codes(["600001"])
    calls2 = _install(monkeypatch, lambda host: _ok())
    fetcher.fetch_raw_by_codes(["600003"])
    assert [c[0] for c in calls2] == [_BACKUP], "冷却中的域名不该被再次请求"


def test_all_hosts_broken_still_fail_open(monkeypatch):
    """fail-open: 全部域名都在冷却中时仍要尝试, 否则上游恢复后永久哑火"""
    fetcher._broken_hosts.update({_PRIMARY: 10 ** 12, _BACKUP: 10 ** 12})
    calls = _install(monkeypatch, lambda host: _ok())
    out = fetcher.fetch_raw_by_codes(["600004"])
    assert len(out) == 1, "全冷却时仍应尝试并成功(fail-open)"
    assert [c[0] for c in calls] == [_PRIMARY], "全冷却时也应从首选域名开始试"


# ---------------------------------------------------------------- ④ rc 语义

def test_rc_nonzero_switches_host_but_does_not_mark_broken(monkeypatch):
    """rc != 0 是数据层错误(域名是通的) → 换域名重试但**不得**标记域名坏"""
    def handler(host):
        return {"rc": 1, "data": None} if host == _PRIMARY else _ok()

    calls = _install(monkeypatch, handler)
    out = fetcher.fetch_raw_by_codes(["600005"])
    assert len(out) == 1
    assert [c[0] for c in calls] == [_PRIMARY, _BACKUP]
    assert fetcher._broken_hosts == {}, "rc 错误不应把域名拉黑"


def test_all_hosts_rc_nonzero_raises(monkeypatch):
    _install(monkeypatch, lambda host: {"rc": 1, "data": None})
    with pytest.raises(RuntimeError) as ei:
        fetcher.fetch_raw_by_codes(["600006"])
    assert "rc=1" in str(ei.value)


def test_empty_diff_from_all_hosts_raises_empty(monkeypatch):
    """rc=0 但无数据 → 走到函数级「返回空」判定, 调用方据此降级"""
    _install(monkeypatch, lambda host: {"rc": 0, "data": {"diff": []}})
    with pytest.raises(RuntimeError) as ei:
        fetcher.fetch_raw_by_codes(["600007"])
    assert "返回空" in str(ei.value)


# ---------------------------------------------------------------- ⑤ 多批

def test_multi_batch_does_not_retry_dead_host(monkeypatch):
    """61 只 = 2 批。首批暴露首选域名坏 → 第二批经冷却直接走备用(每批只打 1 次)"""
    def handler(host):
        if host == _PRIMARY:
            raise ConnectionResetError("挂")
        return _ok()

    calls = _install(monkeypatch, handler)
    out = fetcher.fetch_raw_by_codes(_codes(61))
    assert len(out) == 2, "两批各返回 1 行"
    hosts = [c[0] for c in calls]
    assert hosts.count(_PRIMARY) == 1, "首选域名只该被打一次(后续靠冷却跳过)"
    assert hosts.count(_BACKUP) == 2, "备用域名应服务两批"


# ---------------------------------------------------------------- ⑥ 字段契约

def test_fields_still_full_config_fields(monkeypatch):
    """换域名不得顺手改坏 fields —— 必须整段 config.FIELDS(含 f615/f17/f630)"""
    seen = []

    def fake_urlopen(req, timeout=5, context=None):
        seen.append(req.full_url or req.get_full_url())
        return _JsonResp(_ok())

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", fake_urlopen)
    fetcher.fetch_raw_by_codes(["600001"])
    assert seen, "应发出请求"
    for fld in ("f615", "f17", "f630", "f616", "f617", "f618"):
        assert fld in seen[0], "点查 fields 缺 %s → bidChange 退 f3 事故" % fld
    assert urllib.parse.quote_plus(config.FIELDS) in seen[0], (
        "fields 必须整段 = config.FIELDS(勿手写子集)")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
