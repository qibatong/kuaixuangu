# -*- coding: utf-8 -*-
"""
services/stock_search.py 单测：拼音首字母 + 名录索引口径。

为什么值得单独测：首字母算法是**纯函数、无外部依赖**，但它错一个字母
只表现为「搜不到」，不报错、不打日志 —— 正是本项目最防的「静默」形态。
所以把边界表与代表性名称焊成断言。

2026-09-27 v4.11.63 建。背景：网上流传的 26 区间边界表**在 Y/Z 段是错的**，
会把「银/行/业/药/伊/亚」判成 Z；本表是对着一级汉字升序性质现场反推的。
"""
from app.services import stock_search as ss


def test_bound_table_strictly_increasing():
    """边界必须严格递增 —— 否则分桶结果取决于遍历顺序，属静默错乱。"""
    vals = [v for v, _ in ss._BOUND]
    assert vals == sorted(vals), "拼音边界表必须严格升序"
    assert len(set(vals)) == len(vals), "拼音边界表不得有重复码点"
    # 23 组（无 i / u / v：一级汉字里没有以这三个字母开头的字）
    assert len(ss._BOUND) == 23
    assert 'I' not in [g for _, g in ss._BOUND]
    assert 'U' not in [g for _, g in ss._BOUND]
    assert 'V' not in [g for _, g in ss._BOUND]


def test_bound_yz_regression():
    """★ 回归锚点：Y/Z 段旧表是错的，这两个值不许被"改回流传版"。"""
    d = dict((g, v) for v, g in ss._BOUND)
    assert d['Y'] == -11847, "Y 段边界必须是反推出的 -11847（流传版 -12347 会把银判成 Z）"
    assert d['Z'] == -11055, "Z 段边界必须是反推出的 -11055（流传版 -12138 会把银判成 Z）"


def test_y_initial_chars_not_misjudged_as_z():
    """★ 这条就是旧表的具体故障现象：一批 y 声母字被判成 Z。
    ⚠️ 特意**不放「行」** —— 它是多音字(xíng/háng)，算法取 xíng 得 X，见下方局限用例。"""
    for ch in "银业药伊亚压":
        got = ss.initial_of(ch)
        assert got == 'Y', "「%s」应判 Y，实际 %r" % (ch, got)


def test_documented_limitation_heteronym():
    """★ 已知局限（**显式记录，不是待修缺陷**）：多音字只取一级汉字表里那一个读音。
    「行」判 X(xíng) ⇒「平安银行」得 PAYX 而不是 PAYH。
    影响面有限：六位代码搜索、中文名子串搜索都不受影响；
    且首字母匹配是「包含」语义 ⇒ 输 "pay" 仍能命中。"""
    assert ss.py_initials('平安银行') == 'PAYX'
    assert ss.py_initials('平安').startswith('PA')


def test_level1_names():
    """一级汉字（GB2312 区 16~55）走边界法，零字典表。"""
    cases = {
        '贵州茅台': 'GZMT', '宁德时代': 'NDSD', '隆基绿能': 'LJLN',
        '东方财富': 'DFCF', '中际旭创': 'ZJXC', '寒武纪': 'HWJ',
        '澳弘电子': 'AHDZ', '华瓷股份': 'HCGF', '工业富联': 'GYFL',
        '比亚迪': 'BYD', '万华化学': 'WHHX', '药明康德': 'YMKD',
        '三花智控': 'SHZK', '汇川技术': 'HCJS', '紫金矿业': 'ZJKY',
        '长江电力': 'CJDL', '伊利股份': 'YLGF', '圣邦股份': 'SBGF',
    }
    for name, exp in cases.items():
        assert ss.py_initials(name) == exp, "%s 期望 %s 实际 %s" % (name, exp, ss.py_initials(name))


def test_extra_table_covers_level2_chars_in_stock_names():
    """二级汉字（区 56~87）边界法不可判 —— 靠 _EXTRA 补；这些是真实 A 股名里的高频字。"""
    cases = {
        '天齐锂业': 'TQLY',   # 锂
        '赣锋锂业': 'GFLY',
        '华友钴业': 'HYGY',   # 钴
        '洛阳钼业': 'LYMY',   # 钼
        '钒钛股份': 'FTGF',   # 钒/钛
        '片仔癀':   'PZH',    # 癀
        '獐子岛':   'ZZD',    # 獐
        '东莞控股': 'DGKG',   # 莞（东D 莞G 控K 股G）
        '*ST鑫光': 'XG',      # 鑫（去掉 * 与 ST 后仍可判）
    }
    for name, exp in cases.items():
        nm = name.replace('*', '').replace('ST', '')
        assert ss.py_initials(nm) == exp, "%s 期望 %s 实际 %s" % (nm, exp, ss.py_initials(nm))


def test_extra_table_shape():
    """补充表必须是 单字→单个 A~Z 字母，且无重复键（重复说明有人手改错了）。"""
    assert len(ss._EXTRA) >= 100, "补充表应收录全部 111 个不可判字"
    for k, v in ss._EXTRA.items():
        assert len(k) == 1 and '\u4e00' <= k <= '\u9fff', "键必须是单个汉字: %r" % (k,)
        assert v in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' and len(v) == 1, "值必须是单个大写字母: %r" % (v,)
    # 补充表里的字理应本来就判不出（否则是冗余，说明边界表出了问题）
    bad = [k for k in ss._EXTRA if k.encode('gbk') and ss._code_of(k) and ss._LO <= ss._code_of(k) <= ss._HI]
    assert not bad, "这些字本该被边界法覆盖，不该出现在补充表: %s" % (bad,)


def test_undecidable_returns_empty_not_fake_letter():
    """★ 判不出就**弃权**（返回 ''），绝不猜一个字母凑数 —— 本项目「防静默」纪律。"""
    assert ss.initial_of('') == ''
    assert ss.initial_of(None) == ''
    assert ss.initial_of('A') == ''        # ASCII：不是汉字，弃权
    assert ss.initial_of('1') == ''
    assert ss.initial_of('①') == ''        # 符号区
    # 生僻到不在补充表、也不在一级汉字区的字：弃权而不是硬给一个字母
    assert ss.initial_of('𠀀') == ''


def test_search_empty_query_returns_empty():
    """空查询必须返回空，不能把 5561 只全倒出来（那会把前端下拉撑爆）。
    ★ 注意：这三条走的是「查询词为空 ⇒ 提前 return」的分支，**不碰 DB**。"""
    assert ss.search('') == []
    assert ss.search('   ') == []
    assert ss.search(None) == []


class _FakeIndex:
    """注入假名录，让排序/夹取逻辑可在**不连库**的前提下被测。
    （`_index()` 只在本进程 TTL 过期时才回库 ⇒ 预置 rows + 新鲜 ts 即可短路。）"""

    ROWS = [
        ('600519', '贵州茅台', '白酒', 'GZMT'),
        ('000858', '五粮液', '白酒', 'WLY'),
        ('002466', '天齐锂业', '锂电', 'TQLY'),
        ('300750', '宁德时代', '锂电', 'NDSD'),
        ('601398', '工商银行', '银行', 'GSYX'),
        ('000001', '平安银行', '银行', 'PAYX'),
        ('605058', '澳弘电子', 'PCB', 'AHDZ'),
    ]

    def __enter__(self):
        import time
        self._old = (ss._INDEX['rows'], ss._INDEX['ts'])
        ss._INDEX['rows'] = list(self.ROWS)
        ss._INDEX['ts'] = time.time()
        return self

    def __exit__(self, *a):
        ss._INDEX['rows'], ss._INDEX['ts'] = self._old
        return False


def test_search_by_code():
    with _FakeIndex():
        r = ss.search('605058')
        assert r and r[0]['code'] == '605058', "代码精确应排第一"
        assert r[0]['name'] == '澳弘电子'
        # 代码前缀
        codes = [x['code'] for x in ss.search('6005')]
        assert '600519' in codes
        # 三位数字也能前缀命中
        assert {x['code'] for x in ss.search('000')} >= {'000858', '000001'}


def test_search_by_pinyin_initials():
    with _FakeIndex():
        assert [x['code'] for x in ss.search('tqly')][0] == '002466'
        assert [x['code'] for x in ss.search('TQLY')][0] == '002466'
        assert [x['code'] for x in ss.search('gzmt')][0] == '600519'
        # 拼音前缀（少打几个字母）
        assert [x['code'] for x in ss.search('nd')][0] == '300750'


def test_search_by_chinese_name():
    with _FakeIndex():
        assert [x['code'] for x in ss.search('茅台')][0] == '600519'
        assert [x['code'] for x in ss.search('锂业')][0] == '002466'
        # 只按「名称」匹配，**不掺 board/概念**（宁德时代板块是锂电但名字里没有「锂」）
        assert {x['code'] for x in ss.search('锂')} == {'002466'}
        assert ss.search('锂电') == [] or all('锂电' in x['name'] for x in ss.search('锂电'))


def test_search_ranking_code_before_pinyin():
    """同分排序稳定；且代码精确命中必须压过任何模糊命中。"""
    with _FakeIndex():
        r = ss.search('600519')
        assert len(r) == 1 and r[0]['code'] == '600519'
        # 排序键含 code 升序 ⇒ 结果可复现（同输入同输出）
        a = [x['code'] for x in ss.search('银行')]
        b = [x['code'] for x in ss.search('银行')]
        assert a == b
        assert '000001' in a and '601398' in a


def test_search_limit_clamped():
    """limit 夹到 [1,50]；非数字/越界值不抛异常。"""
    with _FakeIndex():
        assert len(ss.search('银行', 1)) == 1
        assert len(ss.search('银行', 999)) <= 50
        assert len(ss.search('银行', 0)) >= 1        # 0 → 夹到 1
        assert len(ss.search('银行', -5)) >= 1
        ss.search('银行', 'x')                        # 非数字：不抛
        ss.search('银行', None)                       # None：不抛
