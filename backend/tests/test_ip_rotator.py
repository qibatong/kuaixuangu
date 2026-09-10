"""IP 轮询单元测试: 严格 RR 轮询 + 失败惩罚跳过 + 线程并发安全 + 空 IP 池 backward compat"""
import os
import sys
import threading
import unittest
from unittest.mock import patch, MagicMock

# 显式设空 IP 池,避免污染真实环境变量
os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 2026-09-10: 轮换已从 fetcher 下沉到 app/core/net.py(供 kpl/hot_rank/sector_rotation 共用),
# 本测试改为从新位置导入; 旧路径 app.services._net._ROTATOR 已不存在。
from app.core import net as _net
from app.core.net import IPRotator as _IPRotator, ip_binding as _ip_binding
from app.core.net import http_get as _http_get
from app.core.net import _LOCAL as _IP_LOCAL, _patched_create_connection


class TestIPRotatorEmpty(unittest.TestCase):
    """无 IP 池: 走系统默认, 无副作用"""

    def setUp(self):
        self.rot = _IPRotator([])

    def test_empty_pool_returns_none(self):
        self.assertIsNone(self.rot.acquire())


class TestIPRotatorRR(unittest.TestCase):
    """严格 RR: 每次 acquire 都切下一个"""

    def setUp(self):
        self.rot = _IPRotator(["1.1.1.1", "2.2.2.2", "3.3.3.3"])

    def test_strict_rr_order(self):
        seq = [self.rot.acquire() for _ in range(7)]
        self.assertEqual(seq, ["1.1.1.1", "2.2.2.2", "3.3.3.3",
                               "1.1.1.1", "2.2.2.2", "3.3.3.3", "1.1.1.1"])

    def test_two_ips_alternate(self):
        rot = _IPRotator(["1.1.1.1", "2.2.2.2"])
        seq = [rot.acquire() for _ in range(6)]
        self.assertEqual(seq, ["1.1.1.1", "2.2.2.2", "1.1.1.1",
                               "2.2.2.2", "1.1.1.1", "2.2.2.2"])


class TestIPRotatorPenalty(unittest.TestCase):
    """失败惩罚: 连续失败 N 次跳过该 IP, 成功恢复"""

    def setUp(self):
        self.rot = _IPRotator(["1.1.1.1", "2.2.2.2", "3.3.3.3"])

    def test_fail_twice_skips_until_success(self):
        # 正常 RR 起点 1.1.1.1
        self.assertEqual(self.rot.acquire(), "1.1.1.1")
        # 1.1.1.1 连续失败 2 次 → 进入惩罚
        self.rot.report_fail("1.1.1.1")
        self.rot.report_fail("1.1.1.1")
        # 后续 RR 跳过 1.1.1.1
        seq = [self.rot.acquire() for _ in range(6)]
        self.assertNotIn("1.1.1.1", seq)
        self.assertEqual(seq, ["2.2.2.2", "3.3.3.3", "2.2.2.2",
                               "3.3.3.3", "2.2.2.2", "3.3.3.3"])
        # 1.1.1.1 成功一次 → 恢复(惩罚清除后可被选中)
        self.rot.report_success("1.1.1.1")
        got = self.rot.acquire()
        self.assertIn(got, ["2.2.2.2", "3.3.3.3", "1.1.1.1"])

    def test_all_penalized_still_returns(self):
        rot = _IPRotator(["1.1.1.1", "2.2.2.2"])
        rot.report_fail("1.1.1.1"); rot.report_fail("1.1.1.1")
        rot.report_fail("2.2.2.2"); rot.report_fail("2.2.2.2")
        # 全被惩罚 → 不硬卡, 放行 RR 下一个
        self.assertIn(rot.acquire(), ["1.1.1.1", "2.2.2.2"])

    def test_single_fail_does_not_penalize(self):
        self.rot.report_fail("1.1.1.1")  # 只失败 1 次, 不到阈值
        seq = [self.rot.acquire() for _ in range(9)]
        self.assertIn("1.1.1.1", seq)


class TestIPRotatorThreadSafe(unittest.TestCase):
    """多线程并发 acquire: RR index 共享锁, 无重复/错乱"""

    def setUp(self):
        self.rot = _IPRotator(["1.1.1.1", "2.2.2.2"])

    def test_concurrent_acquire_distributes(self):
        results = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            results.append(self.rot.acquire())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 10 次 acquire 在 2 个 IP 间轮换(互斥锁保证无重复 index)
        self.assertEqual(len(results), 10)
        self.assertEqual(sorted(set(results)), ["1.1.1.1", "2.2.2.2"])
        self.assertEqual(results.count("1.1.1.1"), 5)
        self.assertEqual(results.count("2.2.2.2"), 5)


class TestIPBindingContextManager(unittest.TestCase):
    """_ip_binding 上下文: 取 IP + 成功恢复/失败惩罚"""

    def setUp(self):
        # 临时切到测试 IP 池,跑完恢复
        from app.services import fetcher
        self._orig_rot = _net._ROTATOR
        _net._ROTATOR = _IPRotator(["1.1.1.1", "2.2.2.2"])

    def tearDown(self):
        from app.services import fetcher
        _net._ROTATOR = self._orig_rot

    def test_ip_set_in_threadlocal(self):
        with _ip_binding():
            self.assertIn(getattr(_IP_LOCAL, "bind_ip", None), ["1.1.1.1", "2.2.2.2"])
        # 出 context 清空 bind_ip
        self.assertIsNone(getattr(_IP_LOCAL, "bind_ip", None))

    def test_success_clears_penalty(self):
        from app.services import fetcher
        rot = _net._ROTATOR
        # 第一次 context 抛异常 → 该 IP 失败计数 +1
        ip_fail1 = None
        with self.assertRaises(ValueError):
            with _ip_binding():
                ip_fail1 = getattr(_IP_LOCAL, "bind_ip", None)
                raise ValueError("boom1")
        self.assertIn(ip_fail1, rot._penalty)
        self.assertEqual(rot._penalty[ip_fail1], 1)
        # 第二次同样 IP 失败 → 触发惩罚阈值 2
        rot.report_fail(ip_fail1)
        self.assertEqual(rot._penalty[ip_fail1], 2)
        # 成功清除惩罚
        rot.report_success(ip_fail1)
        self.assertNotIn(ip_fail1, rot._penalty)

    def test_exception_reports_fail(self):
        from app.services import fetcher
        rot = _net._ROTATOR
        with self.assertRaises(ValueError):
            with _ip_binding():
                raise ValueError("boom")
        # 失败惩罚 dict 应有 1 个条目(本次请求的 IP)
        self.assertEqual(len(rot._penalty), 1)


class TestPatchedCreateConnection(unittest.TestCase):
    """socket.create_connection monkey-patch 注入 source_address"""

    def test_no_bind_ip_means_no_binding(self):
        _IP_LOCAL.bind_ip = None
        with patch("app.core.net._orig_create_connection") as mock_orig:
            mock_orig.return_value = MagicMock()
            _patched_create_connection(("example.com", 443), timeout=5)
            mock_orig.assert_called_once_with(("example.com", 443), 5, None)

    def test_bind_ip_injected_as_source_address(self):
        _IP_LOCAL.bind_ip = "9.9.9.9"
        try:
            with patch("app.core.net._orig_create_connection") as mock_orig:
                mock_orig.return_value = MagicMock()
                _patched_create_connection(("example.com", 443), timeout=5)
                mock_orig.assert_called_once_with(("example.com", 443), 5, ("9.9.9.9", 0))
        finally:
            _IP_LOCAL.bind_ip = None


class TestHTTPGetIntegration(unittest.TestCase):
    """_http_get 完整流程: 异常 → IP 惩罚"""

    def setUp(self):
        from app.services import fetcher
        self._orig_rot = _net._ROTATOR
        _net._ROTATOR = _IPRotator(["1.1.1.1", "2.2.2.2"])

    def tearDown(self):
        from app.services import fetcher
        _net._ROTATOR = self._orig_rot

    def test_http_get_calls_urlopen(self):
        mock_req = MagicMock()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = MagicMock()
            with _http_get(mock_req, timeout=10):
                mock_urlopen.assert_called_once()

    def test_http_get_exception_penalizes_ip(self):
        from app.services import fetcher
        mock_req = MagicMock()
        with patch("urllib.request.urlopen", side_effect=RuntimeError("blocked")):
            with self.assertRaises(RuntimeError):
                _http_get(mock_req, timeout=10)
            # 应有 1 个 IP 被惩罚
            self.assertEqual(len(_net._ROTATOR._penalty), 1)


class TestEmptyPoolBackwardCompat(unittest.TestCase):
    """空 IP 池: _http_get 行为应与原 urlopen 完全一致(不绑 IP)"""

    def setUp(self):
        from app.services import fetcher
        self._orig_rot = _net._ROTATOR
        _net._ROTATOR = _IPRotator([])  # 空

    def tearDown(self):
        from app.services import fetcher
        _net._ROTATOR = self._orig_rot

    def test_no_bind_ip_when_empty(self):
        mock_req = MagicMock()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = MagicMock()
            with _http_get(mock_req, timeout=10):
                pass
        # 无 IP 池时不应设置 thread-local bind_ip
        self.assertFalse(hasattr(_IP_LOCAL, "bind_ip") and _IP_LOCAL.bind_ip)


if __name__ == "__main__":
    unittest.main(verbosity=2)
