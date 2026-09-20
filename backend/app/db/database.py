# -*- coding: utf-8 -*-
"""
数据库层: 建表 + WAL + 老库自动迁移
===================================
所有表结构与历史版本的自动迁移逻辑集中在此。
"""
import json
import sqlite3
import time

from ..core import config, logger

log = logger.get_logger(__name__)


def get_conn():
    # timeout=10 → busy_timeout 10s: 高并发下多个写者排队等待, 而不是立刻抛
    # "database is locked"(2026-09-09 开盘雪崩时该参数为默认的 0, 写冲突直接失败)
    conn = sqlite3.connect(config.DB_FILE, timeout=10)
    return conn


def init_db():
    """建表与老库迁移, 幂等, 服务启动时调用一次"""
    conn = sqlite3.connect(config.DB_FILE)
    cur = conn.cursor()
    # WAL 模式(持久化): 读写并发不互锁; busy 超时避免锁等待报错
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_date TEXT NOT NULL,
            batch_time TEXT NOT NULL,
            ts INTEGER NOT NULL,
            action TEXT NOT NULL,
            markets TEXT NOT NULL,
            filters TEXT NOT NULL,
            stock_count INTEGER NOT NULL,
            auto_applied INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS batch_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            probability INTEGER NOT NULL,
            confidence INTEGER NOT NULL,
            bid_change REAL NOT NULL,
            real_change REAL NOT NULL,
            entity_change REAL NOT NULL,
            bid_turnover REAL NOT NULL,
            warn_type INTEGER NOT NULL,
            circulation_mv REAL NOT NULL,
            industry TEXT,
            concept TEXT,
            bid_amt REAL NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batch_stocks_batch ON batch_stocks(batch_id)")
    # 老库迁移: 明细增加竞价/昨日成交占比列
    bcols = [r[1] for r in cur.execute("PRAGMA table_info(batch_stocks)").fetchall()]
    if "bid_ratio" not in bcols:
        cur.execute("ALTER TABLE batch_stocks ADD COLUMN bid_ratio REAL")
    # 老库迁移: 明细增加抢筹标记列(2026-09-01 抢筹口径改版: 左视图抢筹=右视图竞价抢筹代码集,
    # 落库时保存打标结论, 历史批次/9:30后锁定名单回看仍能显示 🔥 抢筹)
    if "qiangchou" not in bcols:
        cur.execute("ALTER TABLE batch_stocks ADD COLUMN qiangchou INTEGER NOT NULL DEFAULT 0")
    # 老库迁移: 抢筹明细 JSON(2026-09-09 左视图要区分竞额/涨幅/末秒抢筹并看幅度)
    # 结构: {"t":"amt+chg","a":1.23,"c":0.85,"l":0.4,"x":"竞额抢筹 1.23%；...","f":0}
    if "qc_detail" not in bcols:
        cur.execute("ALTER TABLE batch_stocks ADD COLUMN qc_detail TEXT")
    # 2026-09-11 P0-3: 缺失字段打标(逗号分隔的 camelCase 键, 如 "warnType,bidTurnover")。
    # 背景: _safe_num 把 None 兜成 0 以绕过 NOT NULL 约束, 但 0 是有业务含义的实测值
    #   (异动 0 级 / 换手 0%), 于是"未知"伪装成了"实测 0" —— 9/11 现涨全 0 即此类误导。
    # 落库仍存 0(保证 NOT NULL 与排序), 同时记下哪些字段是"兜底出来的",
    # 读侧据此还原为 null(前端显示「—」), 实现"未知 ≠ 0"。见 history._num_or_mark。
    if "miss_fields" not in bcols:
        cur.execute("ALTER TABLE batch_stocks ADD COLUMN miss_fields TEXT")
    # 用户表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    # 老库迁移: users 增加邀请码/邀请关系列
    ucols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "invite_code" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN invite_code TEXT")
    if "invited_by" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN invited_by INTEGER")
    if "phone" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if "email" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "filter_prefs" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN filter_prefs TEXT")
    if "is_admin" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "expire_at" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN expire_at INTEGER NOT NULL DEFAULT 0")
    if "member_level" not in ucols:
        # 会员等级: 0=免费试用 1=付费会员 2=VIP老师(管理后台指定)
        cur.execute("ALTER TABLE users ADD COLUMN member_level INTEGER NOT NULL DEFAULT 0")
    # 个人资料扩展(2026-08-16): 微信名(客户画像/管理后台识别) + 备注
    if "wx_name" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN wx_name TEXT")
    if "remark" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN remark TEXT")
    # 会员专属付款备注(2026-08-16): 月费用户记录付款时间/方式/凭证等,
    # 区别于通用 remark (内部备注), 仅管理员可改
    if "pay_remark" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN pay_remark TEXT")
    # 注册防刷/自邀识别(2026-08-17): 注册 IP + UA hash
    if "register_ip" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN register_ip TEXT")
    if "register_ua" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN register_ua TEXT")
    # 邮箱认证(2026-08-17): 老用户默认已认证(1), 新注册置 0 强制验证后才可登录
    if "email_verified" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")
    if "email_verify_code" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN email_verify_code TEXT")
    if "email_verify_expire" not in ucols:
        cur.execute("ALTER TABLE users ADD COLUMN email_verify_expire INTEGER NOT NULL DEFAULT 0")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
    # 登录 Token 持久化表(进程重启不失效, 支持「记住我」30 天)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expire_ts INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_expire ON tokens(expire_ts)")
    # 2026-08-17: tokens 加 revoked 列(新登录踢旧会话不再 DELETE, 保留行以便前端区分
    # 「被另一设备顶出」vs「自然过期」→ 弹出"账号已在另一设备登录"通知)
    tcols = [r[1] for r in cur.execute("PRAGMA table_info(tokens)").fetchall()]
    if "revoked" not in tcols:
        cur.execute("ALTER TABLE tokens ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0")
    # 启动顺手清一次过期 token
    cur.execute("DELETE FROM tokens WHERE expire_ts < ?", (int(time.time()),))
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    # 系统设置表(key-value, JSON 值): 评分权重等管理配置
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    # 每日一字涨停统计(竞价时段市场快照, 供趋势查看)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_yizi (
            date TEXT PRIMARY KEY,
            yizi_count INTEGER NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL
        )
    """)
    # 板块轮动历史快照: 每日收盘后保存板块强度 Top10, 形成轮动数据基础
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_sector_top (
            date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'kpl',
            boards TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, source)
        )
    """)
    # 人气热榜每日快照: date+source 唯一, 回看历史人气榜
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hot_rank_history (
            date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'kpl',
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, source)
        )
    """)
    # 龙虎榜每日快照: date 唯一, 回看历史龙虎榜
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lhb_history (
            date TEXT NOT NULL,
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date)
        )
    """)
    # 连板梯队每日快照: date+pid_type 唯一, 回看历史连板天梯
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ladder_history (
            date TEXT NOT NULL,
            pid_type INTEGER NOT NULL,
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, pid_type)
        )
    """)
    # 竞价异动日终快照: date+tab 唯一, 供竞价异动页按日期回看历史
    # tab: seal(竞价委买)/boom(竞价爆量)/qiangcang(竞价抢筹list20)/
    #      yest_zt(昨日涨停)/yest_broken(昨断板)/broken_yest(昨炸板)/broken_today(今炸板)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auction_daily_history (
            date TEXT NOT NULL,
            tab TEXT NOT NULL,
            list TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, tab)
        )
    """)
    # 历史日现涨(当日收盘涨跌幅)持久化(2026-08-22): 一库存所有历史交易各股收盘涨幅,
    # 历史回看直接读本表, 无需再请求东财日K接口
    cur.execute("""
        CREATE TABLE IF NOT EXISTS close_change_history (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            pct REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (date, code)
        )
    """)
    # 旧库升级: 已存在且无 source 列时补上(单字段主键), 历史数据默认 kpl
    try:
        cur.execute("ALTER TABLE daily_sector_top ADD COLUMN source TEXT NOT NULL DEFAULT 'kpl'")
    except Exception:
        pass   # 列已存在, ignore
    # 旧库主键检测: 早期版本 daily_sector_top 是单字段 date 主键, 加 source 列后主键仍为
    # (date), 会导致不同 source 同日期 INSERT OR REPLACE 互相覆盖 —— 必须重建为 (date, source)
    try:
        cols = cur.execute("PRAGMA table_info(daily_sector_top)").fetchall()
        pk_cols = [c[1] for c in cols if c[5]]
        if pk_cols != ["date", "source"]:
            cur.execute("DROP TABLE IF EXISTS daily_sector_top_migrate")
            cur.execute("""
                CREATE TABLE daily_sector_top_migrate (
                    date TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'kpl',
                    boards TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    PRIMARY KEY (date, source)
                )
            """)
            cur.execute(
                "INSERT OR IGNORE INTO daily_sector_top_migrate (date, source, boards, ts) "
                "SELECT date, COALESCE(source, 'kpl'), boards, ts FROM daily_sector_top")
            cur.execute("DROP TABLE daily_sector_top")
            cur.execute("ALTER TABLE daily_sector_top_migrate RENAME TO daily_sector_top")
            log.info("daily_sector_top 主键迁移完成 (date,source)")
    except Exception:
        pass   # 兼容异常场景, 不阻塞启动
    # 9:20 竞价时点快照(全市场): 用于 9:25 计算涨幅加速度
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_920 (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            bid_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        )
    """)
    # 多时点竞价快照归档(历史回放): 9:15/9:20/9:25 全市场快照, 每日积累形成回放库
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_bid (
            date TEXT NOT NULL,
            time_point TEXT NOT NULL,
            code TEXT NOT NULL,
            bid_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            name TEXT,
            bid_buy_amt REAL NOT NULL DEFAULT 0,
            float_mv REAL NOT NULL DEFAULT 0,
            free_mv REAL NOT NULL DEFAULT 0,
            pre_fd_amount REAL NOT NULL DEFAULT 0,
            fd_to_yesterday REAL NOT NULL DEFAULT 0,
            auc_turnover REAL NOT NULL DEFAULT 0,
            warn_type INTEGER NOT NULL DEFAULT 0,
            auc_main_net REAL NOT NULL DEFAULT 0,
            auc_pre_vol_ratio REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (date, time_point, code)
        )
    """)
    # 老库迁移: snapshot_bid 增加 名称/委买额/流通市值 列(回放/抢筹计算用)
    bcols2 = [r[1] for r in cur.execute("PRAGMA table_info(snapshot_bid)").fetchall()]
    if "name" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN name TEXT")
    if "bid_buy_amt" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN bid_buy_amt REAL NOT NULL DEFAULT 0")
    if "float_mv" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN float_mv REAL NOT NULL DEFAULT 0")
    # 2026-08-19: 实际流通市值(东财 f117 自由流通, 开盘啦"实际流通"同口径) — 竞价异动流通列改造
    if "free_mv" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN free_mv REAL NOT NULL DEFAULT 0")
    if "board" not in bcols2:
        # 概念/行业标签(f103概念优先, f100行业兜底), 供 昨日涨停/昨断板 等概念列补全
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN board TEXT")
    # 2026-09-18 (v4.11.30): 异动等级(东财 f630) 随定格一起落库。
    #   背景: 主人要求把评分 17% 权重的「异动」改回东财 f630, 但**东财点查
    #   (push2 ulist.np) 长期被封**(实测 RemoteDisconnected) → 定格链路(9:25 后选股)
    #   的名单行来自 snapshot_bid, 而 from_snapshot 原先不设 warn_type, 补丁源又拿不到
    #   f630 → 全市场落 default 0.18, 17% 权重退化成常数(9/8、9/17 两次事故)。
    #   而**全市场 clist(push2dycalc) 是通的且 config.FIELDS 本就带 f630**
    #   (2026-09-19 复测: 全市场 5917 只里 2280 只非 0 = 38.5%; 沪深主板 3487 只里
    #    1268 只非 0 = 36.4%。取值域 0~14, 3/4/5 档齐全。⚠️ 早前记的"5856 只里
    #    1268 只非 0 = 21.7%"是把主板口径的非 0 数错配到全市场总数上, 已订正)
    #   → 在 9_25 采集那一秒顺手落库,
    #   定格链路就有了权威异动等级, 且与「9:25 定格一次定生死」的口径一致。
    #   语义: 0=无异动(与历史 f630=0 同义, 评分落 default), 列 NOT NULL 故历史行回填 0。
    if "warn_type" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN warn_type INTEGER NOT NULL DEFAULT 0")
    # 2026-09-20: 昨日封单额 + 封昨比(猫爪 screening 原生提供, 无需自算)。
    #   来源: screening.params 不传 symbols → 全市场一次拿到的 pre_fd_amount /
    #         fd_to_yesterday(见 meoz_client._SCREENING_FIELDS)。
    #   语义: pre_fd_amount    昨日封单额(元) —— 昨日涨停/一字封单强度
    #         fd_to_yesterday  封昨比 = 今日封单 ÷ 昨日封单 —— 官方成品值, 免自算;
    #                          仅"今日有封单且昨日有封单"时有值, 其余 null
    #   用途: 供"昨日封单强度/封单同比"类因子与展示; 与今日 bid_buy_amt 分列不混。
    #   默认 0(NOT NULL) —— 与历史行语义一致(老行无此数据 = 0 = 无封单)。
    #   🔴 血泪教训: 原先误加 pre_fd_break_amount / pre_fd_break_times 两列 —— 猫爪
    #     **无此字段**, 传了即 422 → 整个 screening 调用失败。screening 字段以
    #     ~/.meoz/cache/openapi.json 为准, 禁止凭截图推断字段名。
    if "pre_fd_amount" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN pre_fd_amount REAL NOT NULL DEFAULT 0")
    if "fd_to_yesterday" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN fd_to_yesterday REAL NOT NULL DEFAULT 0")
    # 2026-09-20: 真实竞价换手率(猫爪 screening.auc_turnover, 自由流通股本口径, %)。
    #   用途: 评分 activity 因子(权重 32%)直接读落库官方成品, 不再自算 bid_amt/free_mv。
    #   默认 0(NOT NULL) —— 与历史行语义一致(老行无此数据 = 0 = 无竞价换手)。
    if "auc_turnover" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN auc_turnover REAL NOT NULL DEFAULT 0")
    # 2026-09-20 (v5): 竞价主力净额(猫爪 fundflow_kp.auction_main_net_amount, 元, 9:25 起更新)。
    #   用途: 评分 17% 异动分新因子层(主人拍板: 删加速度修正+低开 gate, 换主力净额)。
    #   覆盖实测(2026-09-18 全市场): 非零仅 32% —— 主力净额是"有大单才有值",
    #   0 = 竞价无大单异动(评分走该层 default, 不当惩罚)。
    #   默认 0(NOT NULL) —— 历史行/采集失败行 = 0 = 无信号。
    if "auc_main_net" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN auc_main_net REAL NOT NULL DEFAULT 0")
    # 2026-09-20: 竞昨量比(猫爪 daily_auc.auc_to_pre_auc_vol_ratio = 今竞价成交量 ÷ 昨竞价成交量)。
    #   用途: 评分 17% 异动分「量比层」官方成品值 —— 替换原「本地自算(今额/昨额)」。
    #   ★ 与自算对比(9-18/9-17 实测): 相关系数 0.854, 相对差异中位仅 1.6%;
    #     官方字段更精确且无「昨额<100万失真爆炸」(官方 max 26 vs 自算 582)。
    #   ★ 零额外调用量: daily_auc 本就在采集链(源②)里, 只是 fields 多加一字段。
    #   默认 0(NOT NULL) —— 历史行/采集失败行 = 0 = 无官方值 → 量比层回退自算。
    if "auc_pre_vol_ratio" not in bcols2:
        cur.execute("ALTER TABLE snapshot_bid ADD COLUMN auc_pre_vol_ratio REAL NOT NULL DEFAULT 0")
    # ⚠️ 历史遗留列(2026-09-20): pre_fd_break_amount / pre_fd_break_times —— 曾误加,
    #   猫爪无此字段(传即 422)。现已无任何代码读写, 值恒为默认 0。
    #   本机 SQLite 3.7.17 **不支持 DROP COLUMN**(需 3.35+) → 保留不动, 不影响查询与业务。
    #   未来库重建/SQLite 升级时自然消除。
    # 密码重置令牌
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reset_tokens_token ON reset_tokens(token)")
    # 竞价抢筹结果快照(开盘啦 Type4 竞价时段抓取持久化, 非竞价时段读库展示)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qc_snapshot (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            real_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            qc_delta REAL NOT NULL DEFAULT 0,
            bid_turnover REAL NOT NULL DEFAULT 0,
            bid_change REAL NOT NULL DEFAULT 0,
            float_mv REAL NOT NULL DEFAULT 0,
            board TEXT,
            bid_ratio REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        )
    """)
    # 老库迁移: qc_snapshot 增加 竞额/昨比 列(2026-08-14, 抢筹表"竞价换手"改为"竞额/昨比")
    qcols = [r[1] for r in cur.execute("PRAGMA table_info(qc_snapshot)").fetchall()]
    if "bid_ratio" not in qcols:
        cur.execute("ALTER TABLE qc_snapshot ADD COLUMN bid_ratio REAL NOT NULL DEFAULT 0")
    # 最后一秒抢筹高频采样(9:24:55-9:25:03 每秒一次, ts 记实际时刻;
    # 计算时用序列做"差值回退", 对抗接口延迟)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_lastsec (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            bid_change REAL NOT NULL DEFAULT 0,
            bid_amt REAL NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code, ts)
        )
    """)
    # 概念映射表(2026-08-21): code -> 开盘啦概念(前N个拼接)
    # concept_refresh 每30分钟从开盘啦采集**当日所有竞价/上榜实时股票**的概念,
    # 全量写本表; 前端竞价各接口直接读本表即可, 不再每次请求实时打开盘啦。
    # 对比"写到各 tab 列表 JSON": 本表能覆盖实时表格(如竞价爆量盘中有407只,
    # 而 9:26 落库仅97只)新增的股票, 避免新出现股票概念读不到。<...>
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_concept (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            board TEXT,
            board_full TEXT,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        )
    """)
    # 老库迁移: stock_concept 增加 board_full 列(2026-08-27: 存开盘啦全量概念, 供 AI 预测悬浮展示全部)
    sc_cols = [r[1] for r in cur.execute("PRAGMA table_info(stock_concept)").fetchall()]
    if "board_full" not in sc_cols:
        cur.execute("ALTER TABLE stock_concept ADD COLUMN board_full TEXT")
    # 老库迁移: batches 增加 user_id 列(用户隔离)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(batches)").fetchall()]
    if "user_id" not in cols:
        cur.execute("ALTER TABLE batches ADD COLUMN user_id INTEGER")
    # 老库迁移: batches 增加 auto_applied 列(2026-08-16: 9:26 自动应用标记, 区别用户主动 lock/filter)
    if "auto_applied" not in cols:
        cur.execute("ALTER TABLE batches ADD COLUMN auto_applied INTEGER NOT NULL DEFAULT 0")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_user ON batches(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_auto ON batches(auto_applied)")
    # 异步任务队列(Phase1 建立, worker 进程消费; save_batch 默认仍同步, 切异步后启用)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            done_at INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status)")

    # ---------- 2026-08-27: 股性功能 ----------
    # 每日涨停/炸板明细存档: 供「历史封板率 / 次日溢价 / 炸板反包 / 连板基因」统计。
    # 来源: 东财 flash 历史池(limit_up_pool/limit_up_broken)每日盘后落库;
    #       过去一年由回补脚本逐日(YYYY-MM-DD)回填。
    # 口径: is_limit=1 之意最终封住(涨停池), is_limit=0 之意最终炸板(炸板池)。
    #       历史接口只给「当日最终态」, 盘中首封时间/封单等细粒度仅从上线起累积。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS limit_history (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            is_limit INTEGER NOT NULL DEFAULT 1,
            zt INTEGER NOT NULL DEFAULT 1,
            zbc INTEGER NOT NULL DEFAULT 0,
            change REAL NOT NULL DEFAULT 0,
            reason TEXT,
            ts INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_limit_history_code ON limit_history(code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_limit_history_date ON limit_history(date)")
    # 个股日K缓存: 回补/现算「次日溢价、大阴线、反包」时免重复拉东财。
    # 一行一只股票一整段日K(JSON); ts 记录落库时间。字段: date(基准日,k线含T-119..T日)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_kline (
            code TEXT PRIMARY KEY,
            day_data TEXT NOT NULL,
            ts INTEGER NOT NULL
        )
    """)
    # 昨日成交额落库(2026-09-10 主人拍板: 去掉多源兜底链, 改"收盘后落库 + 全天读库")
    # 昨日成交额是**静态历史数据**, 原实现每次选股实时逐只拉东财日K(4 源兜底链切换),
    # 既触发风控又因多源口径不一导致数据跳变。改为: 每交易日 15:10 收盘后批量拉一次
    # 全市场写本表(按 code 覆盖写), 之后全天直接读库 —— 零网络、零兜底、零风控。
    #
    # 覆盖写语义(无需读时算"昨日是哪天"):
    #   - 9/10 15:10 写入 T=9/10 数据 → 9/11 盘中读取即为"昨日"(正确)
    #   - 9/11 15:10 覆盖为 T=9/11    → 9/11 收盘后读取即为"今日"(正确, 收盘后语义)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS yday_amount (
            code TEXT PRIMARY KEY,
            tdate TEXT NOT NULL,
            amount REAL,
            prev_amount REAL,
            chg REAL,
            ts INTEGER NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_yday_amount_tdate ON yday_amount(tdate)")
    # ---------- 2026-09-12 P1: 全市场评分物化表 ----------
    # 评分 5 因子(竞涨34/竞价换手32/竞价强度17/流通市值11/昨涨6)的输入**全部在 9:25 定格**,
    # 无一依赖实时行情 → 可在竞价结束后对全市场(≈5557只)一次性算好分数落本表。
    #
    # 收益: 用户改筛选条件时不再重跑取数与评分, 只对本表做一次 SELECT + 纯 CPU 过滤
    #       (毫秒级), 且名单**天然幂等**(同一交易日同一条件 → 同一名单)。
    #
    # 口径铁律: 本表**只由 DB 已落库数据算出**(snapshot_bid 定格 + yday_amount 昨涨
    #   + bid_strength), **绝不触碰网络行情源** —— 否则东财一限流, 物化结果就随
    #   可用性漂移, 幂等与"对东财免疫"两个目标同时失效。
    #
    # 幂等: PRIMARY KEY(date, code) + INSERT OR REPLACE(SQLite 3.7 不支持 ON CONFLICT)。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_score_daily (
            date          TEXT NOT NULL,
            code          TEXT NOT NULL,
            name          TEXT,
            probability   INTEGER NOT NULL,
            confidence    INTEGER NOT NULL,
            bid_change    REAL,
            bid_amt       REAL,
            bid_vol       REAL,
            float_mv      REAL,
            bid_turnover  REAL,
            strength      REAL,
            warn_type     INTEGER NOT NULL DEFAULT 0,
            yday_chg      REAL,
            prev_close    REAL,
            auction_price REAL,
            is_st         INTEGER NOT NULL DEFAULT 0,
            is_zt_yday    INTEGER NOT NULL DEFAULT 0,
            board         TEXT,
            industry      TEXT,
            concept       TEXT,
            rank          INTEGER,
            detail        TEXT,
            ts            INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ssd_date_rank ON stock_score_daily(date, rank)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ssd_date_prob ON stock_score_daily(date, probability DESC)")
    # 流通市值日频缓存(P2-2, 2026-09-12): 把 float_mv/free_mv/name/board 这类**静态
    # 基础数据**与竞价采集解耦 —— 东财熔断时 TickPlus 补进来的全市场票没有市值,
    # 从本表(≤15天)或腾讯 f44 补, 否则 float_mv=0 会被市值门槛当小盘股全部误杀。
    # 数值列**可空**: 拿不到写 NULL, 绝不写 0(0=实测值, NULL=未知, 同 P0-3 铁律)。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_float_mv_daily (
            date     TEXT NOT NULL,
            code     TEXT NOT NULL,
            name     TEXT,
            float_mv REAL,
            free_mv  REAL,
            board    TEXT,
            src      TEXT,
            ts       INTEGER,
            PRIMARY KEY (date, code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mv_code_date ON stock_float_mv_daily(code, date)")
    # 股性画像落库(方案B): 每日盘后一次性算好全部画像, 排行直读此表避免实时逐股重算。
    # profile 为 compute_profile 全量 JSON; score/zt_count/name 供排行排序与搜索筛选。
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_temper_profile (
            code TEXT PRIMARY KEY,
            name TEXT,
            score REAL NOT NULL DEFAULT 0,
            zt_count INTEGER NOT NULL DEFAULT 0,
            profile TEXT NOT NULL,
            ts INTEGER NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_temper_score ON stock_temper_profile(score DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_temper_name ON stock_temper_profile(name)")

    # ---------- 2026-08-25: limitUp/stSuspend 语义反转(旧=true时剔除, 新=true时只看)
    # 迁移幂等: 用 settings 表 mig_filter_sem_flip_v2 标记, 标记已存在则跳过.
    # 迁移内容:
    #   1) 所有用户 filter_prefs JSON 中的 limitUp / stSuspend 布尔值取反
    #   2) settings.default_filters 中的 limitUp / stSuspend 布尔值取反
    #   3) 同步: DEFAULT_FILTERS_DEFAULT 内置默认值已同步(见 api/admin.py)
    #   4) 前端 localStorage 锁定筛选: 由前端运行时迁移(见 stores/stocks.js)
    mig_key = "mig_filter_sem_flip_v2"
    mig_done = cur.execute("SELECT 1 FROM settings WHERE key=?", (mig_key,)).fetchone()
    if not mig_done:
        _flip_count = 0
        for (uid, raw) in cur.execute("SELECT id, filter_prefs FROM users WHERE filter_prefs IS NOT NULL AND filter_prefs != ''").fetchall():
            try:
                obj = json.loads(raw)
            except (TypeError, ValueError):
                continue
            changed = False
            for k in ("limitUp", "stSuspend"):
                if isinstance(obj.get(k), bool):
                    obj[k] = not obj[k]
                    changed = True
            if changed:
                cur.execute("UPDATE users SET filter_prefs=? WHERE id=?",
                            (json.dumps(obj, ensure_ascii=False), uid))
                _flip_count += 1
        # settings.default_filters 取反
        df_row = cur.execute("SELECT value FROM settings WHERE key=?", ("default_filters",)).fetchone()
        if df_row:
            try:
                df = json.loads(df_row[0])
                changed = False
                for k in ("limitUp", "stSuspend"):
                    if isinstance(df.get(k), bool):
                        df[k] = not df[k]
                        changed = True
                if changed:
                    cur.execute("UPDATE settings SET value=?, updated_at=? WHERE key=?",
                                (json.dumps(df, ensure_ascii=False), int(time.time()), "default_filters"))
            except (TypeError, ValueError):
                pass
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            (mig_key, json.dumps({"t": int(time.time()), "users_flipped": _flip_count}, ensure_ascii=False), int(time.time())))
        log.info("[mig_filter_sem_flip_v2] done, users_flipped=%d", _flip_count)

    conn.commit()
    conn.close()


# ==================== 异步任务队列(worker 进程消费) ====================
def enqueue_task(type_, payload, ts=None):
    """投递异步任务, 返回任务 id; 失败返回 None(不抛异常)"""
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO task_queue (type, payload, status, created_at) VALUES (?,?, 'pending', ?)",
            (type_, json.dumps(payload, ensure_ascii=False), int(ts or time.time())))
        conn.commit()
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return tid
    except Exception as e:
        log.warning("任务投递失败 type=%s err=%s", type_, e)
        return None


def get_pending_tasks(limit=20):
    """取 pending 任务列表(worker 轮询消费)"""
    try:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, type, payload FROM task_queue WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,)).fetchall()
        out = [dict(r) for r in rows]
        conn.close()
        return out
    except Exception as e:
        log.warning("取 pending 任务失败 err=%s", e)
        return []


def mark_task_done(tid, failed=False):
    """标记任务完成/失败(幂等)"""
    try:
        conn = get_conn()
        conn.execute("UPDATE task_queue SET status=?, done_at=? WHERE id=?",
                     ("failed" if failed else "done", int(time.time()), tid))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("标记任务失败 id=%s err=%s", tid, e)
    log.info("数据库初始化/迁移完成: %s", config.DB_FILE)
