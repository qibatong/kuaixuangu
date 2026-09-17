# -*- coding: utf-8 -*-
"""
pytest 公共夹具: 临时数据库 + 数据源 Mock(不依赖外部网络) + TestClient
运行: cd backend && python -m pytest tests/ -v
"""
import os
import tempfile

# 必须在 import app 之前设置临时数据库路径
# 幂等守卫(2026-08-16 修复): test_stocks.py 有 `from conftest import MOCK_RAW`,
# pytest 收集与普通 import 可能让 conftest 模块执行两次 → 若每次重建 _tmp,
# 第二次会把 BID_DB_PATH 覆盖为新的空文件, 而 app.config.DB_FILE 已固定为
# 第一次的文件 → app 建表在 A, 测试直连 env 读到 B(0字节) → no such table: users
if not os.environ.get("BID_DB_PATH"):
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", prefix="kuaixuan_test_", delete=False)
    os.environ["BID_DB_PATH"] = _tmp.name
else:
    _tmp = None
# 测试日志写到临时目录, 避免污染/占用真实 logs/app.log(Windows 文件锁导致 PermissionError)
os.environ["BID_LOG_DIR"] = tempfile.gettempdir()

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import fetcher, scorer

# ---------- Mock 行情数据(字段与东财 FIELDS 对应) ----------
# 数值设计为能通过默认筛选: 竞价涨幅<=7 / f630<=4(非首板) / 流通30-100亿 / 价格<30 / 竞价额>3000万
MOCK_RAW = [
    {"f2": 18.50, "f3": 3.20, "f4": 3.10, "f5": 150000.0, "f6": 2800.0,
     "f8": 5.50, "f10": 1.80, "f12": "600001", "f14": "测试甲",
     "f17": 18.90, "f18": 17.90, "f20": 5.0e10, "f21": 4.0e9,
     "f100": "软件服务", "f102": "广东", "f103": "AI概念",
     "f615": 3.50, "f616": 5.0e7, "f617": 300.0, "f618": 400.0, "f630": 3},
    {"f2": 9.80, "f3": 5.10, "f4": 5.00, "f5": 90000.0, "f6": 1600.0,
     "f8": 4.20, "f10": 1.50, "f12": "000002", "f14": "测试乙",
     "f17": 9.90, "f18": 9.30, "f20": 2.0e10, "f21": 5.0e9,
     "f100": "医药", "f102": "上海", "f103": "创新药",
     "f615": 4.80, "f616": 4.0e7, "f617": 220.0, "f618": 300.0, "f630": 4},
    {"f2": 22.30, "f3": 2.20, "f4": 2.10, "f5": 60000.0, "f6": 900.0,
     "f8": 1.10, "f10": 0.80, "f12": "300003", "f14": "测试丙",
     "f17": 22.60, "f18": 21.60, "f20": 8.0e9, "f21": 6.0e9,
     "f100": "半导体", "f102": "江苏", "f103": "芯片",
     "f615": 2.20, "f616": 3.5e7, "f617": 80.0, "f618": 100.0, "f630": 2},
    {"f2": 25.60, "f3": 6.80, "f4": 6.60, "f5": 200000.0, "f6": 4000.0,
     "f8": 8.00, "f10": 2.50, "f12": "000004", "f14": "测试丁",
     "f17": 26.10, "f18": 24.90, "f20": 4.0e10, "f21": 8.0e9,
     "f100": "汽车", "f102": "浙江", "f103": "新能源车",
     "f615": 5.50, "f616": 6.0e7, "f617": 350.0, "f618": 450.0, "f630": 4},
]


@pytest.fixture(scope="session")
def monkeypatch_session():
    """session 级 monkeypatch(标准 monkeypatch 是 function 级)"""
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="session", autouse=True)
def mock_data_source(monkeypatch_session):
    """全局 mock 数据源: 不发起任何真实网络请求"""

    def fake_ensure_cache(action, fs, before930):
        if action == "lock" and not before930:
            return None, "9:30 后禁止重新选股"
        import time
        fetcher._cache[fs] = {"raw": MOCK_RAW, "ts": time.time()}
        return fetcher._cache[fs]["raw"], None

    def fake_yesterday_amounts(codes):
        # 真实结构: {code: [T日全天额(万元), T-1日全天额(万元)]}
        return {c: [20000.0, 15000.0] for c in codes}

    monkeypatch_session.setattr(fetcher, "ensure_cache", fake_ensure_cache)
    monkeypatch_session.setattr(fetcher, "fetch_yesterday_amounts", fake_yesterday_amounts)
    # 2026-09-09 起选股主链路 = picker.pipeline, 名单源在竞价窗口外是**9:25 定格快照**
    # (auction_snapshot.load_snapshot_full), 而测试库里没有定格数据 → 不桩会返回
    # "名单源无数据" 让所有接口级用例(落库/幂等/去重/推送/缓存)集体失败。
    # 这里用 MOCK_RAW 造一份等价定格快照(字段见 QuoteRow.from_snapshot)。
    def fake_load_snapshot_full(date=None):
        out = {}
        for s in MOCK_RAW:
            code = s.get("f12")
            if not code:
                continue
            out[code] = {
                "code": code, "name": s.get("f14"),
                "pre_close": s.get("f18") or s.get("f4"),
                "bid_change": s.get("f615"),
                "bid_amt": (s.get("f616") or 0) / 1e4,     # 元 → 万元(快照口径)
                "bid_vol": s.get("f617"),
                "price": s.get("f2"), "change": s.get("f3"),
                "open": s.get("f17"), "float_mv": s.get("f21"),
                "industry": s.get("f100"), "concept": s.get("f103"),
            }
        return out

    from app.services import auction_snapshot as _asnap
    monkeypatch_session.setattr(_asnap, "load_snapshot_full", fake_load_snapshot_full)
    # 选股主链路(picker.pipeline)的**补丁源**东财点查/腾讯点查、以及昨日涨幅日K:
    # 若不桩, 测试进程会对 push2 / qt.gtimg.cn 发起真实请求(本地网络不通 → 熔断
    # 冷却 600s → 用例集体超时)。竞价窗口名单源(腾讯全市场)同样桩掉。
    def fake_by_codes(codes, *a, **kw):
        want = set(codes or [])
        hit = [dict(s) for s in MOCK_RAW if s.get("f12") in want]
        return hit or [dict(s) for s in MOCK_RAW]

    monkeypatch_session.setattr(fetcher, "fetch_raw_by_codes", fake_by_codes)
    monkeypatch_session.setattr(fetcher, "fetch_tencent_by_codes", fake_by_codes)
    monkeypatch_session.setattr(fetcher, "fetch_tencent_market",
                                lambda fs, *a, **kw: [dict(s) for s in MOCK_RAW])
    monkeypatch_session.setattr(fetcher, "fetch_yesterday_changes",
                                lambda codes, *a, **kw: {c: 1.5 for c in (codes or [])})
    # 2026-09-07 is_first_board 改造(昨涨停名单判据): scorer 会调 fetcher.get_yesterday_zt_codes()
    # 拉 push2ex 涨停池(真实网络)。测试一律桩成 None → 走 f103 概念降级路径(与旧行为一致,
    # 保证既有用例零回归); 专项测试(test_zt_pool_filter.py)单独 monkeypatch 验证名单路径。
    monkeypatch_session.setattr(fetcher, "get_yesterday_zt_codes", lambda: None)
    # 2026-09-04 spotMap 预热线程: client fixture 的 TestClient(with)会触发 app startup
    # → start_spot_prewarm 起真线程。若在预热窗口(9:26-15:05)内跑测试, 线程会真拉腾讯全市场
    # 网络(测试进程内日志噪音/守护线程退出冲突)。测试环境直接屏蔽线程启动(空转也无意义),
    # 单次拉取函数一并桩掉双保险。
    monkeypatch_session.setattr(fetcher, "start_spot_prewarm", lambda: None)
    monkeypatch_session.setattr(fetcher, "_spot_prewarm_once", lambda: None)
    # 2026-09-04 KPL 首屏预热线程(main.py startup 同刻启动): 同样屏蔽, 防止测试进程
    # 在窗口(9:15-15:05)内真拉开盘啦外网打 KPL 配额/污染日志。
    try:
        from app.services import kpl as _kpl_mod
        monkeypatch_session.setattr(_kpl_mod, "start_kpl_prewarm", lambda: None)
        # 桩掉前先留存真实函数: 预热**行为**测试(如 test_market_brief_cache_20260904
        # 验证 market_brief 被纳入预热且 delete 用对 key)需要调真实实现;
        # 真实实现内的 loader 由各用例自行 monkeypatch, 不会打外网。
        # 注意: 必须 raising=False —— 该属性原本不存在, Monkeypatch 默认 raising=True
        # 会对"新增属性"抛 AttributeError(被下面 except 静默吞掉, 极难排查)。
        monkeypatch_session.setattr(_kpl_mod, "_kpl_prewarm_once_real",
                                    _kpl_mod._kpl_prewarm_once, raising=False)
        monkeypatch_session.setattr(_kpl_mod, "_kpl_prewarm_once", lambda: None)
    except Exception:
        pass


@pytest.fixture(scope="session")
def _pytest_session():
    yield


@pytest.fixture(scope="session")
def client(mock_data_source):
    """TestClient: 每个测试间共享(进程内单实例), 数据库独立
    显式依赖 mock_data_source(2026-09-04): session 级数据源桩必须早于 app startup 建立,
    否则 lifespan 里 start_spot_prewarm 会在预热窗口(9:26-15:05)起真线程拉全市场网络
    (autouse 与 client 实例化顺序不保证, 窗口内跑测试曾真拉腾讯 5556 只)"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_client_cookies(client):
    """每个测试后清空 TestClient 的 cookie jar(2026-08-30 修复):
    /api/login 已写 kx_token cookie, TestClient(httpx) 会自动保存并带到后续请求,
    导致无 token 的 auth-check 用例在全量运行时误返回 200(被 cookie 鉴权放行)。
    必须在每个测试后清空, 保证用例相互隔离。"""
    yield
    try:
        client.cookies.clear()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def mock_rate_limits(monkeypatch_session):
    """测试环境放开限流(注册防刷 + 每IP每分钟 + 同IP每日注册数), 避免多文件用例互相干扰"""
    from app.services import security
    monkeypatch_session.setattr(security, "register_allowed", lambda ip: True)
    monkeypatch_session.setattr(security, "register_ip_day_allowed", lambda ip: True)
    # 兼容 2026-09-04 新签名 rate_allow(ip, uid=None) — 测试默认全放行, 不走真实限流路径
    monkeypatch_session.setattr(security, "rate_allow", lambda ip, uid=None: True)


@pytest.fixture(scope="session", autouse=True)
def mock_bj_auction_window(monkeypatch_session):
    """固定北京 9:25(竞价窗口内)时间语义(2026-09-07 db91b5a/4b44500 起需要):
    选股链路的竞额定格判定 = in_auction_window() AND _bj_hm()<9:30, 依赖
    真实时刻的用例会随运行时间漂移(白天竞价窗口内才过 / 晚上跑全挂——MOCK_RAW 的
    f616 语义只在窗口内生效, 窗口外 bid_amt 只认 9:25 定格快照 day_bid_amt, 测试
    不传则=0 → 竞价额门槛把全部候选滤掉 → 返回空名单)。
    默认固定 9:25 → 既有 MOCK_RAW f616 用例任何时刻稳定; 需要窗口外/9:30 后语义
    的专项用例显式 monkeypatch 覆盖(如 test_bid_amt_offwindow_no_fallback)。
    """
    monkeypatch_session.setattr(scorer, "in_auction_window", lambda: True)
    monkeypatch_session.setattr(scorer, "_bj_hm", lambda: 9 * 60 + 25)
    # before930=True: 集成测试默认走 ensure_cache 老路径(快照候选池由
    # test_snapshot_candidate 纯函数单测覆盖; 需盘后/9:30 后语义的用例显式 mock)
    monkeypatch_session.setattr(scorer, "bj_now", lambda: (9, 25, True))


@pytest.fixture(scope="session", autouse=True)
def mock_pick_window_guard(client):
    """选股闸门(settings `pick_window_guard`)测试环境默认**关闭**(2026-09-16 新增)。

    理由与 mock_bj_auction_window 同类: 闸门依赖**真实时刻**(交易日 9:00-9:15 与
    9:25:00-9:25:35 两段, 2026-09-17 v4.11.27 重做后口径) 与**当日 9_25 是否落库**,
    而测试库既没有当日定格快照(load_snapshot_full 被桩成 MOCK_RAW), 运行时刻又是随机的
    —— 不关闭会让所有 /api/stocks 用例命中拦截而集体红(表征为"时段相关的偶发失败",
    极难排查)。

    闸门自身的用例在 tests/test_pick_window_guard.py 里显式把开关置 1 +
    注入时刻(monkeypatch app.api.stocks._pick_blocked_reason 的 now), 覆盖完整。

    默认值说明: 生产 `settings.get(SWITCH, 1)` = 1(库中无该键 → 开); 本 fixture 只在
    测试库写入 0, 不影响生产默认。"""
    from app.services import settings as _st
    _st.set("pick_window_guard", 0)


@pytest.fixture(scope="session")
def create_user_token(client):
    """注册关闭后(2026-08-25合规)测试建用户改用 service 层, 不经注册接口。
    返回 callable(username, password, phone, email) -> (uid, token, invite_code)
    默认邮箱已验证可直接登录。"""
    import uuid
    from app.db import database
    from app.services import users, security

    def _make(username=None, password="Test123456", phone=None, email=None,
              member_level=1, email_verified=1, expire_at=None):
        uname = username or ("t_" + uuid.uuid4().hex[:8])
        if phone is None:
            phone = "138" + str(uuid.uuid4().int % 100000000).zfill(8)
        if email is None:
            email = uuid.uuid4().hex[:8] + "@test.local"
        uid = users.create_user(uname, password, phone=phone, email=email,
                                email_verified=email_verified)
        users.set_member_level(uid, member_level)
        if expire_at:
            users.set_expire(uid, expire_at)
        token = security.issue_token(uid)
        # 取邀请码
        inv = users.ensure_invite_code(uid)
        return {"uid": uid, "token": token, "username": uname,
                "password": password, "email": email, "phone": phone,
                "invite_code": inv or None}
    return _make


@pytest.fixture(scope="session")
def first_user(create_user_token):
    """注册一个唯一用户(避免与其他测试的用户名冲突), 返回 (token, username, invite_code)"""
    u = create_user_token()
    return u["token"], u["username"], u["invite_code"]


@pytest.fixture(scope="session")
def second_user(create_user_token):
    """第二个普通用户(非管理员, 免费试用 member_level=0), 用于权限类测试.
    注册已关闭, service 层创建不带邀请码。"""
    u = create_user_token(member_level=0)
    return u["token"], u["username"]


@pytest.fixture(scope="session")
def vip_user(client, first_user):
    """VIP 用户 (member_level=2), 用于需要 VIP 权限的接口测试"""
    from app.services import users as users_svc
    from app.db import database
    username = first_user[1]
    conn = database.get_conn()
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    uid = row[0] if row else None
    if uid:
        users_svc.set_member_level(uid, 2)
    return first_user


@pytest.fixture(autouse=True)
def _isolate_fetcher_globals():
    """每个用例前后隔离 fetcher 的模块级全局状态(2026-09-10 新增)。

    背景: 昨日成交额的短路条件取决于 K线源熔断态(fetcher._fetch_yesterday_amount_one /
    fetch_yesterday_amounts)。任何把 eastmoney_kline / tencent_kline 打进熔断的用例
    (如 test_stocks 的 K线用例)都会让后续用例里"数据源健康"的断言集体短路失败
    —— 表征为**单文件绿、全量跑红**的顺序耦合。

    这里在用例前后快照并还原熔断状态表与坏主机表, 消除顺序耦合; 用例内部对 _HEALTH
    的直接改写(如 test_circuit_breaker)依然在本用例内生效, 只是不再泄漏给下一个用例。
    """
    import copy
    with fetcher._health_lock:
        health_snap = copy.deepcopy(fetcher._HEALTH)
    broken_snap = copy.deepcopy(fetcher._broken_hosts)
    yield
    with fetcher._health_lock:
        fetcher._HEALTH.clear()
        fetcher._HEALTH.update(health_snap)
    fetcher._broken_hosts.clear()
    fetcher._broken_hosts.update(broken_snap)

