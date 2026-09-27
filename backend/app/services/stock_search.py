# -*- coding: utf-8 -*-
"""
全市场股票快速搜索（移动端 H5「🔍 跳股」的数据源）
====================================================================
主人 2026-09-27《快选移动端追加清单》§三：「全局能搜到股票并跳转」——
此前搜索只散在管理后台/历史/股性页里，行情页想直接看某只票找不到入口。

支持三种输入（可混用）：
  · 6 位代码        「600519」「6050」
  · 中文名称片段    「茅台」「锂业」
  · 拼音首字母      「gzmt」「tqly」

★ 数据来源：**本地 SQLite，零网络**。
  `snapshot_bid` 最新 `9_25` 定格（全市场 5561 行，含 code/name/board）为主；
  为空时回落 `stock_float_mv_daily`（同为全市场，含 name/board）。
  ⇒ 不新增任何外部调用，也不占用开盘啦 8 万次/日配额。

★ 为什么索引建在**后端**：拼音首字母要用 `str.encode('gbk')`，Python 内置；
  前端算不了（没有 GBK 编码器，也拿不到全市场名录）。仓库里前后端都**没有**
  任何拼音依赖（已 grep 确认），本模块因此零新增依赖。

--------------------------------------------------------------------
拼音首字母算法
--------------------------------------------------------------------
GB2312 **一级汉字（区 16~55）本身按拼音升序排列** ⇒ 用「每个字母组里第一个一级汉字」
的码点当边界，即可判定任意一级汉字的首字母（零依赖、无需字典表）。

⚠️ 网上流传的那张 26 区间表**在 Y/Z 段是错的**（Y=-12347/Z=-12138）：会把
   「银/行/业/药/伊/亚」等一大批 y 声母字误判成 Z（银→Z）。本模块的边界是对着
   一级汉字升序性质**现场反推**的：Y=-11847、Z=-11055，其余 21 个与流传版一致
   （这也反过来印证了只有那两条是错的）。反推脚本与验证方法见 _probe 里的探测记录。

⚠️ **二级汉字（区 56~87）按部首排序，边界法天然不可判**（锂/钴/钼/钛 全落在那里，
   而它们是 A 股名称里的高频字）。故补一张 `_EXTRA` 表：来源是**对真实 5561 个
   股票名做「不可判字」频次统计**，共 111 种 / 239 次 —— 不是凭空猜的字表。
   ⇒ 名称可判率：仅边界法 95.8%（5330/5561）→ 加补充表 100%。

⚠️ 已知局限（如实写在注释里，不假装完美）：
   · **多音字**取一级汉字表里那一个读音。典型：`行` 判成 X（xíng），
     「平安银行」得 PAYX 而非 PAYH。六位代码与中文名搜索不受影响。
   · 新增罕见字出现时需补 `_EXTRA`：重跑 `_probe/dv3_badchars.sh` 拿新字清单。
"""
import threading
import time
import unicodedata

from ..core import logger
from ..db import database

log = logger.get_logger(__name__)

# ---------------------------------------------------------------- 拼音首字母
# 每个字母组「第一个一级汉字」的 GBK 码点（= b0*256+b1-65536）。
# 无 i / u / v 组 —— 一级汉字里没有以这三个字母开头的字。
_BOUND = [
    (-20319, 'A'),   # 啊
    (-20283, 'B'),   # 芭
    (-19775, 'C'),   # 擦
    (-19218, 'D'),   # 搭
    (-18710, 'E'),   # 蛾
    (-18526, 'F'),   # 发
    (-18239, 'G'),   # 噶
    (-17922, 'H'),   # 哈
    (-17417, 'J'),   # 击
    (-16474, 'K'),   # 喀
    (-16212, 'L'),   # 垃
    (-15640, 'M'),   # 妈
    (-15165, 'N'),   # 拿
    (-14922, 'O'),   # 哦
    (-14914, 'P'),   # 啪
    (-14630, 'Q'),   # 期
    (-14149, 'R'),   # 然
    (-14090, 'S'),   # 撒
    (-13318, 'T'),   # 塌
    (-12838, 'W'),   # 挖
    (-12556, 'X'),   # 昔
    (-11847, 'Y'),   # 压   ★ 流传版写 -12347（错，会把 银/行/业/药 判成 Z）
    (-11055, 'Z'),   # 匝   ★ 流传版写 -12138（错）
]
_LO = _BOUND[0][0]
_HI = -10247                    # 一级汉字末字「座」= 0xD7F9

# 二级汉字补充表（GB2312 区 56~87，按部首排序 ⇒ 边界法不可判）。
# 来源：对真实全市场 5561 个 A 股名称统计「不可判字」，共 111 种 / 239 次，全量收录。
# 维护：出现新字时重跑 _probe/dv3_badchars.sh 取新字清单，按 字→首字母 补进来。
_EXTRA = {
    '鑫': 'X', '孚': 'F', '钛': 'T', '锂': 'L', '昊': 'H', '瀚': 'H',
    '晟': 'S', '睿': 'R', '圳': 'Z', '麒': 'Q', '麟': 'L', '奕': 'Y',
    '怡': 'Y', '宸': 'C', '晖': 'H', '泓': 'H', '鹭': 'L', '钴': 'G',
    '钰': 'Y', '泸': 'L', '祺': 'Q', '濮': 'P', '榕': 'R', '皓': 'H',
    '璟': 'J', '崧': 'S', '昇': 'S', '迦': 'J', '曦': 'X', '岱': 'D',
    '衢': 'Q', '嵘': 'R', '钼': 'M', '璞': 'P', '沐': 'M', '甬': 'Y',
    '楹': 'Y', '莞': 'G', '钽': 'T', '魅': 'M', '炜': 'W', '铖': 'C',
    '獐': 'Z', '锝': 'D', '螳': 'T', '螂': 'L', '浔': 'X', '锆': 'G',
    '蟒': 'M', '琚': 'J', '柘': 'Z', '榈': 'L', '馨': 'X', '恺': 'K',
    '灏': 'H', '憬': 'J', '萃': 'C', '韬': 'T', '黛': 'D', '瀛': 'Y',
    '鹄': 'H', '琏': 'L', '荃': 'Q', '聆': 'L', '禧': 'X', '玑': 'J',
    '珈': 'J', '翎': 'L', '祯': 'Z', '鳌': 'A', '鹞': 'Y', '烨': 'Y',
    '蠡': 'L', '渥': 'W', '胤': 'Y', '骐': 'Q', '濠': 'H', '瑜': 'Y',
    '毓': 'Y', '熵': 'S', '玮': 'W', '崴': 'W', '珂': 'K', '兖': 'Y',
    '琪': 'Q', '癀': 'H', '珑': 'L', '蜻': 'Q', '蜓': 'T', '懋': 'M',
    '珀': 'P', '徕': 'L', '炀': 'Y', '蟠': 'P', '麾': 'H', '蒽': 'E',
    '昕': 'X', '茗': 'M', '荻': 'D', '昀': 'Y', '冢': 'Z', '岹': 'T',
    '昱': 'Y', '淼': 'M', '颀': 'Q', '钜': 'J', '芸': 'Y', '煜': 'Y',
    '琛': 'C', '锴': 'K', '燧': 'S',
}


def _code_of(ch):
    """汉字 → 有符号 GBK 码点（b0*256+b1-65536）；非 GBK / 非汉字返回 None。"""
    try:
        b = ch.encode('gbk')
    except (UnicodeEncodeError, LookupError):
        return None
    if len(b) < 2:
        return None
    return b[0] * 256 + b[1] - 65536


def initial_of(ch):
    """单字 → 拼音首字母大写。**判不出就返回 ''（弃权），绝不猜一个字母凑数。**"""
    if not ch or not ('\u4e00' <= ch <= '\u9fff'):
        return ''
    hit = _EXTRA.get(ch)
    if hit:
        return hit
    c = _code_of(ch)
    if c is None or c < _LO or c > _HI:
        return ''                      # 二级汉字/符号区：不可判
    last = ''
    for v, g in _BOUND:
        if c >= v:
            last = g
        else:
            break
    return last


def py_initials(name):
    """名称 → 拼音首字母串（跳过判不出的字，不插入占位符）。"""
    return ''.join(initial_of(ch) for ch in (name or ''))


# ---------------------------------------------------------------- 名录索引
_INDEX = {'ts': 0.0, 'rows': []}
_INDEX_TTL = 600                    # 10 分钟：名录只在采集/换日时变，无需更勤
_LOCK = threading.Lock()


def _norm(s):
    """NFKC 归一（全角→半角：Ａ→A、６→6）+ 去空白 + 大写字母。"""
    return unicodedata.normalize('NFKC', str(s or '')).replace(' ', '').replace('\u3000', '').strip()


def _load_rows():
    """从本地库取全市场 (code, name, board)。零网络。"""
    conn = database.get_conn()
    try:
        rows = []
        # 主源：snapshot_bid 最新 9_25 定格（生产实测 5561 行、name 100% 非空）
        d = conn.execute(
            "SELECT MAX(date) FROM snapshot_bid WHERE time_point='9_25'").fetchone()
        if d and d[0]:
            rows = conn.execute(
                "SELECT code, name, board FROM snapshot_bid "
                "WHERE date=? AND time_point='9_25' AND code IS NOT NULL",
                (d[0],)).fetchall()
        if not rows:
            # 回落：stock_float_mv_daily（同为全市场，含 name/board）
            d2 = conn.execute("SELECT MAX(date) FROM stock_float_mv_daily").fetchone()
            if d2 and d2[0]:
                rows = conn.execute(
                    "SELECT code, name, board FROM stock_float_mv_daily WHERE date=?",
                    (d2[0],)).fetchall()
                log.warning("股票搜索: snapshot_bid 无 9_25 行, 已回落 stock_float_mv_daily date=%s",
                            d2[0])
        return rows
    finally:
        conn.close()


def _index():
    """名录索引（带 TTL 的进程内缓存）。返回 [(code, name, board, py_compact)]。"""
    now = time.time()
    if _INDEX['rows'] and now - _INDEX['ts'] < _INDEX_TTL:
        return _INDEX['rows']
    with _LOCK:
        now = time.time()
        if _INDEX['rows'] and now - _INDEX['ts'] < _INDEX_TTL:
            return _INDEX['rows']
        raw = _load_rows()
        out = []
        for code, name, board in raw:
            nm = _norm(name)
            out.append((str(code or ''), (name or '').strip(), board or '',
                        py_initials(nm)))
        _INDEX['rows'] = out
        _INDEX['ts'] = now
        log.info("股票搜索名录已重建: %d 只 (TTL=%ds)", len(out), _INDEX_TTL)
        return out


def search(q, limit=20):
    """按 代码 / 名称 / 拼音首字母 搜索；返回 [{code,name,board,py}]。

    排序优先级：代码精确 > 代码前缀 > 拼音前缀 > 名称前缀 > 名称包含 > 拼音包含。
    """
    kw = _norm(q)
    if not kw:
        return []
    try:
        limit = max(1, min(50, int(limit or 20)))
    except (TypeError, ValueError):
        limit = 20

    is_digit = kw.isdigit()
    is_alpha = kw.isalpha() and kw.isascii()
    kw_u = kw.upper()

    hits = []
    for code, name, board, py in _index():
        if is_digit:
            if code == kw:
                score = 0
            elif code.startswith(kw):
                score = 1
            elif kw in code:
                score = 2
            else:
                continue
        elif is_alpha:
            if not py:
                continue
            if py.startswith(kw_u):
                score = 2
            elif kw_u in py:
                score = 5
            else:
                continue
        else:
            nm = _norm(name)
            if nm.startswith(kw):
                score = 3
            elif kw in nm:
                score = 4
            elif board and kw in _norm(board):
                score = 6   # 概念标签命中, 排在名称后面
            elif py and kw_u in py:
                score = 5
            else:
                continue
        hits.append((score, code, name, board, py))

    # 同分按代码升序（主板 6xx/0xx 在前），保证结果稳定、可复现
    hits.sort(key=lambda x: (x[0], x[1]))
    return [{'code': c, 'name': n, 'board': b, 'py': p} for _, c, n, b, p in hits[:limit]]


def index_size():
    """当前名录条数（0 表示尚未建索引）。供探测/自检用。"""
    try:
        return len(_index())
    except Exception:
        return 0
