"""IP 轮询单元测试: 验证 round-robin、失败切换、线程隔离、空 IP 池走系统默认"""
import os
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

# 显式设空 IP 池,避免污染真实环境变量
os.environ["OUTBOUND_IPS"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.fetcher import _IPRotator, _ip_binding, _http_get
from app.services.fetcher import _IP_LOCAL, _patched_create_connection, _orig_create_connection


class TestIPRotatorEmpty(unittest.TestCase):
    """无 IP 池: 走系统默认, 无副作用"""

    def setUp(self):
        # 重新构造一个空 IP Rotator
        self.rot = _IPRotator([])

    def test_empty_pool_returns_none(self):
        self.assertIsNone(self.rot.acquire())

    def test_empty_pool_no_state(self):
        # 不应创建 thread-local state
        self.rot.acquire()
        self.assertFalse(hasattr(_IP_LOCAL, "inited"))


class TestIPRotatorRR(unittest.TestCase):
    """单线程 round-robin + 失败切换"""

    def setUp(self):
        self.rot = _IPRotator(["1.1.1.1", "2.2.2.2", "3.3.3.3"])
        # 重置 thread-local
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def test_rr_order(self):
        # 首次 acquire 返回第一个 IP
        self.assertEqual(self.rot.acquire(), "1.1.1.1")
        # 失败 1 次不切换
        self.rot.report_fail()
        self.assertEqual(self.rot.acquire(), "1.1.1.1")
        # 失败 2 次切下一个
        self.rot.report_fail()
        self.assertEqual(self.rot.acquire(), "2.2.2.2")
        # 成功保持当前 IP
        self.rot.report_success()
        self.assertEqual(self.rot.acquire(), "2.2.2.2")
        # 失败 2 次切下一个
        self.rot.report_fail()
        self.rot.report_fail()
        self.assertEqual(self.rot.acquire(), "3.3.3.3")
        # 失败 2 次切回 1.1.1.1
        self.rot.report_fail()
        self.rot.report_fail()
        self.assertEqual(self.rot.acquire(), "1.1.1.1")

    def test_success_resets_fail_counter(self):
        self.rot.acquire()
        self.rot.report_fail()
        self.rot.report_fail()
        self.rot.report_fail()  # 第 3 次,但前 2 次后已切到 2.2.2.2
        self.assertEqual(self.rot.acquire(), "2.2.2.2")
        # 2.2.2.2 失败 1 次不切
        self.rot.report_fail()
        self.assertEqual(self.rot.acquire(), "2.2.2.2")
        # 成功清零
        self.rot.report_success()
        # 失败 1 次仍不切(fail=1 < 2)
        self.rot.report_fail()
        self.assertEqual(self.rot.acquire(), "2.2.2.2")


class TestIPRotatorThreadLocal(unittest.TestCase):
    """多线程 thread-local 隔离"""

    def setUp(self):
        self.rot = _IPRotator(["1.1.1.1", "2.2.2.2"])
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def test_threads_have_independent_state(self):
        results = {}
        barrier = threading.Barrier(2)

        def worker(name, expected_ip):
            if hasattr(_IP_LOCAL, "inited"):
                delattr(_IP_LOCAL, "inited")
            barrier.wait()  # 同步起跑
            ip = self.rot.acquire()
            results[name] = ip
            time.sleep(0.05)  # 让另一个线程也走完
            ip2 = self.rot.acquire()
            results[name + "_2"] = ip2

        t1 = threading.Thread(target=worker, args=("t1", None))
        t2 = threading.Thread(target=worker, args=("t2", None))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # 两个线程都从 ips[0] 开始(独立 thread-local)
        # 之后也都各报 success,保持不变
        self.assertEqual(results["t1"], "1.1.1.1")
        self.assertEqual(results["t2"], "1.1.1.1")
        self.assertEqual(results["t1_2"], "1.1.1.1")
        self.assertEqual(results["t2_2"], "1.1.1.1")


class TestIPBindingContextManager(unittest.TestCase):
    """_ip_binding 上下文: 切换 + 报告成功/失败"""

    def setUp(self):
        # 临时切到测试 IP 池,跑完恢复
        from app.services import fetcher
        self._orig_rot = fetcher._IP_ROTATOR
        fetcher._IP_ROTATOR = _IPRotator(["1.1.1.1", "2.2.2.2"])
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def tearDown(self):
        from app.services import fetcher
        fetcher._IP_ROTATOR = self._orig_rot
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def test_ip_set_in_threadlocal(self):
        with _ip_binding():
            self.assertEqual(getattr(_IP_LOCAL, "bind_ip", None), "1.1.1.1")
        # 出 context 清空 bind_ip, 但 rot_ip 保留(下次 acquire 从这切)
        self.assertIsNone(getattr(_IP_LOCAL, "bind_ip", None))
        self.assertEqual(getattr(_IP_LOCAL, "rot_ip", None), "1.1.1.1")

    def test_success_resets_fail(self):
        from app.services import fetcher
        # 第一个 context 故意抛异常 → 触发 report_fail
        with self.assertRaises(ValueError):
            with _ip_binding():
                fetcher._IP_ROTATOR.report_fail()  # 1.1.1.1 fail=1
                fetcher._IP_ROTATOR.report_fail()  # 1.1.1.1 fail=2
                raise ValueError("test")
        # 现在 1.1.1.1 fail>=2, 下次 acquire 切到 2.2.2.2
        with _ip_binding():
            self.assertEqual(getattr(_IP_LOCAL, "bind_ip", None), "2.2.2.2")

    def test_exception_propagates_and_reports_fail(self):
        from app.services import fetcher
        # 第一次 context: 故意抛异常
        with self.assertRaises(ValueError):
            with _ip_binding():
                raise ValueError("test")
        # 失败计数应已 +1
        st = fetcher._IP_ROTATOR._state()
        self.assertEqual(st.fail, 1)


class TestPatchedCreateConnection(unittest.TestCase):
    """socket.create_connection monkey-patch 注入 source_address"""

    def setUp(self):
        from app.services import fetcher
        self._orig_rot = fetcher._IP_ROTATOR
        fetcher._IP_ROTATOR = _IPRotator(["9.9.9.9"])
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def tearDown(self):
        from app.services import fetcher
        fetcher._IP_ROTATOR = self._orig_rot
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def test_no_ip_local_means_no_binding(self):
        # 没有 thread-local bind_ip 时, 应透传给原始 create_connection
        with patch("app.services.fetcher._orig_create_connection") as mock_orig:
            mock_orig.return_value = MagicMock()
            _patched_create_connection(("example.com", 443), timeout=5)
            mock_orig.assert_called_once_with(("example.com", 443), 5, None)

    def test_ip_local_injected_as_source_address(self):
        _IP_LOCAL.bind_ip = "9.9.9.9"
        try:
            with patch("app.services.fetcher._orig_create_connection") as mock_orig:
                mock_orig.return_value = MagicMock()
                _patched_create_connection(("example.com", 443), timeout=5)
                mock_orig.assert_called_once_with(("example.com", 443), 5, ("9.9.9.9", 0))
        finally:
            _IP_LOCAL.bind_ip = None


class TestHTTPGetIntegration(unittest.TestCase):
    """_http_get 完整流程: 模拟 urlopen 异常 → IP 切换"""

    def setUp(self):
        from app.services import fetcher
        self._orig_rot = fetcher._IP_ROTATOR
        fetcher._IP_ROTATOR = _IPRotator(["1.1.1.1", "2.2.2.2"])
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def tearDown(self):
        from app.services import fetcher
        fetcher._IP_ROTATOR = self._orig_rot
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def test_http_get_with_ip_binding_calls_urlopen(self):
        mock_req = MagicMock()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = MagicMock()
            with _http_get(mock_req, timeout=10):
                mock_urlopen.assert_called_once()

    def test_http_get_exception_reports_fail(self):
        from app.services import fetcher
        mock_req = MagicMock()
        with patch("urllib.request.urlopen", side_effect=RuntimeError("blocked")):
            with self.assertRaises(RuntimeError):
                _http_get(mock_req, timeout=10)
            # 失败应被记录
            st = fetcher._IP_ROTATOR._state()
            self.assertEqual(st.fail, 1)


class TestEmptyPoolBackwardCompat(unittest.TestCase):
    """空 IP 池: _http_get 行为应与原 urlopen 完全一致(不绑 IP)"""

    def setUp(self):
        from app.services import fetcher
        self._orig_rot = fetcher._IP_ROTATOR
        fetcher._IP_ROTATOR = _IPRotator([])  # 空
        if hasattr(_IP_LOCAL, "inited"):
            delattr(_IP_LOCAL, "inited")

    def tearDown(self):
        from app.services import fetcher
        fetcher._IP_ROTATOR = self._orig_rot

    def test_no_ip_set_when_empty(self):
        mock_req = MagicMock()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = MagicMock()
            with _http_get(mock_req, timeout=10):
                pass
        # 无 IP 池时不应设置 thread-local bind_ip
        self.assertFalse(hasattr(_IP_LOCAL, "bind_ip") and _IP_LOCAL.bind_ip)


if __name__ == "__main__":
    unittest.main(verbosity=2)
