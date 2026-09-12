# -*- coding: utf-8 -*-
"""出站 IP 轮询公共层(2026-09-09 从 fetcher 抽出)

背景
----
⚠️ 2026-09-13 现状更正(原注释说"双网卡双出口", 已过时):
  生产机**只剩 eth0 一张网卡**(辅助网卡已下线), 即:

    eth0 172.22.114.161 (内网) -> 公网出口(见运维记录, 公网 IP 不入库)

  ``OUTBOUND_IPS`` 虽仍是单 IP, 但**出口轮换能力实际已失效**(只剩一个可用源地址)。
  本模块的代码保留(配置为空 = 走 OS 默认单 IP, 行为正确、无副作用),
  但不要再指望"换出口"来绕过封禁。

实测(2026-09-09, 当时仍双出口):
  * 东财 push2 / push2his 对**两个出口都是 HTTP 000**(0.05s RST)
    → 东财是 IDC/AS 级封禁, 换 IP 无效。
  * 新浪 hq.sinajs.cn: eth0=**403**(该公网 IP 已被新浪拉黑) / eth1=**200**
    → 按 IP 封禁的源, 换出口立竿见影(但该出口现已不存在)。
  * 腾讯 qt.gtimg.cn / 开盘啦: 两出口均 200。

补充实测(2026-09-12, 住宅代理横评)—— **代理路线已证伪, 勿再采购**:
  * 同一批住宅代理 IP 打 push2dycalc / push2ex = **200**, 打 push2his / push2
    = **立刻 RST**(0.18~0.43s 远端主动断开) → **同出口、不同接口、结果相反**,
    证明东财封锁发生在**接口/客户端特征层面, 不看来源 IP**。
  * 按竞价频率压测(28 页并发 × 12 轮): 直连 **100% 成功 / 0.46s 每轮**;
    住宅代理 **67% / 18.5s 每轮**, 失败全是 `IncompleteRead`/SSL 握手 —— 代理侧问题。

结论: 封禁的正确解法是**换同语义数据源**(如 TickPlus 补竞价量额),
      不是换出口 IP。轮换仅作为"多出口容灾"手段, 当前环境无用武之地。

设计
----
* 配置: 环境变量 ``OUTBOUND_IPS``(逗号分隔**内网 IP**, 空 = 走 OS 默认单 IP)
* 严格 RR 轮询 + 连续失败惩罚(默认 2 次跳过, 成功即恢复)
* monkey-patch ``socket.create_connection`` 注入 ``source_address``
* ``ip_binding()`` 是 context manager: **只在with 块内**绑定 thread-local IP。
  这是刻意的 —— 不能全局无条件绑定, 否则连 127.0.0.1 的本地请求
  (notify 回调/内部 HTTP) 也会因源地址不匹配而失败。

用法::

    from ..core import net
    with net.ip_binding():
        with urllib.request.urlopen(req, timeout=10) as r:
            ...
"""
import contextlib
import socket as _socket_mod
import threading
import urllib.request

from . import config

# 连续失败多少次后跳过该 IP(成功一次即清零)
IP_FAIL_THRESHOLD = 2

_LOCAL = threading.local()


class IPRotator:
    """IP 轮询: 进程级严格 RR + 失败惩罚。"""

    def __init__(self, ips):
        self.ips = list(ips) if ips else []
        self._rr_lock = threading.Lock()
        self._rr_index = -1
        self._penalty = {}          # ip -> 连续失败次数

    def acquire(self):
        """严格 RR 取下一个健康 IP; 全部被惩罚则放行 RR 下一个(保证有 IP 可用)。"""
        if not self.ips:
            return None
        n = len(self.ips)
        with self._rr_lock:
            for _ in range(n):
                self._rr_index = (self._rr_index + 1) % n
                ip = self.ips[self._rr_index]
                if self._penalty.get(ip, 0) < IP_FAIL_THRESHOLD:
                    return ip
            return self.ips[self._rr_index]

    def report_success(self, ip):
        self._penalty.pop(ip, None)

    def report_fail(self, ip):
        self._penalty[ip] = self._penalty.get(ip, 0) + 1

    def snapshot(self):
        """当前健康状态(运维排查用)"""
        return {"ips": list(self.ips), "penalty": dict(self._penalty)}


_ROTATOR = IPRotator(getattr(config, "OUTBOUND_IPS", []) or [])

_orig_create_connection = _socket_mod.create_connection


def _patched_create_connection(address, timeout=None, source_address=None, **kw):
    ip = getattr(_LOCAL, "bind_ip", None)
    if ip:
        source_address = (ip, 0)
    return _orig_create_connection(address, timeout, source_address, **kw)


# 覆盖 socket 模块本身 + urllib.request.socket(同一引用, 双保险)
_socket_mod.create_connection = _patched_create_connection
urllib.request.socket.create_connection = _patched_create_connection


@contextlib.contextmanager
def ip_binding():
    """带 IP 轮询的 urlopen 上下文; 无 IP 池时为 no-op。

    成功 → report_success(ip); 异常 → report_fail(ip) 并原样抛出。
    """
    ip = _ROTATOR.acquire()
    if not ip:
        yield
        return
    _LOCAL.bind_ip = ip
    try:
        yield
        _ROTATOR.report_success(ip)
    except Exception:
        _ROTATOR.report_fail(ip)
        raise
    finally:
        _LOCAL.bind_ip = None


def rotator():
    return _ROTATOR


def http_get(req, timeout, context=None):
    """``urllib.request.urlopen`` 的 IP 轮询包装。"""
    with ip_binding():
        return urllib.request.urlopen(req, timeout=timeout, context=context)
