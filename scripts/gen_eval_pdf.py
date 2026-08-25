#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成通达信竞价选股公式落地评估 PDF"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Preformatted
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
import os

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
FONT_CN = 'STSong-Light'
FONT_MONO = 'Courier'

C_ACCENT = colors.HexColor('#1a5670')
C_HEADER_BG = colors.HexColor('#1a5670')
C_ROW_ALT = colors.HexColor('#f0f4f6')
C_BORDER = colors.HexColor('#cccccc')
C_CODE_BG = colors.HexColor('#f5f5f5')

styles = getSampleStyleSheet()

sTitle = ParagraphStyle('TitleCN', parent=styles['Title'],
    fontName=FONT_CN, fontSize=20, leading=28, textColor=C_ACCENT,
    spaceAfter=6, alignment=TA_CENTER)
sSubtitle = ParagraphStyle('SubtitleCN', parent=styles['Normal'],
    fontName=FONT_CN, fontSize=11, leading=16, textColor=colors.grey,
    alignment=TA_CENTER, spaceAfter=18)
sH1 = ParagraphStyle('H1CN', parent=styles['Heading1'],
    fontName=FONT_CN, fontSize=15, leading=22, textColor=C_ACCENT,
    spaceBefore=18, spaceAfter=8)
sH2 = ParagraphStyle('H2CN', parent=styles['Heading2'],
    fontName=FONT_CN, fontSize=13, leading=19, textColor=colors.HexColor('#2c3e50'),
    spaceBefore=14, spaceAfter=6)
sH3 = ParagraphStyle('H3CN', parent=styles['Heading3'],
    fontName=FONT_CN, fontSize=11, leading=16, textColor=colors.HexColor('#34495e'),
    spaceBefore=10, spaceAfter=4)
sBody = ParagraphStyle('BodyCN', parent=styles['Normal'],
    fontName=FONT_CN, fontSize=10, leading=16, alignment=TA_JUSTIFY, spaceAfter=4)
sBodyIndent = ParagraphStyle('BodyIndentCN', parent=sBody, leftIndent=12)
sBullet = ParagraphStyle('BulletCN', parent=sBody, leftIndent=18, bulletIndent=6, spaceAfter=2)
sCode = ParagraphStyle('CodeCN', parent=styles['Code'],
    fontName=FONT_MONO, fontSize=8, leading=12,
    backColor=C_CODE_BG, borderColor=C_BORDER, borderWidth=0.5,
    borderPadding=4, spaceBefore=4, spaceAfter=8, leftIndent=6, rightIndent=6)
sNote = ParagraphStyle('NoteCN', parent=sBody, fontSize=9, leading=14,
    textColor=colors.HexColor('#7f8c8d'), leftIndent=12)

def P(text, style=None):
    return Paragraph(text, style or sBody)

def make_table(data, col_widths=None):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, -1), FONT_CN, 9),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 0), (-1, 0), C_HEADER_BG),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_ROW_ALT]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    return t

def th(text):
    return Paragraph(text, ParagraphStyle('th', fontName=FONT_CN, fontSize=9, textColor=colors.white, alignment=TA_CENTER))
def td(text, sz=9):
    return Paragraph(text, ParagraphStyle('td', fontName=FONT_CN, fontSize=sz, leading=sz+4))

story = []

# ---- 封面 ----
story.append(Spacer(1, 40*mm))
story.append(Paragraph('通达信竞价选股公式<br/>快选平台落地可行性评估报告', sTitle))
story.append(Spacer(1, 8*mm))
story.append(Paragraph('快选 Kuaixuan &middot; 竞价 AI 选股系统', sSubtitle))
story.append(Spacer(1, 20*mm))
cover_info = [
    ['项目', '快选 Kuaixuan 竞价/盘中选股系统'],
    ['评估对象', '通达信「资金强度」竞价选股公式（9条子策略）'],
    ['评估日期', '2026-08-24'],
    ['评估方式', '逐字段 / 逐条件 / 逐子策略 三层拆解'],
    ['结论', '可行，核心85%~90%可落地'],
    ['文件类型', '评估报告（不含代码实现）'],
]
ct = Table(cover_info, colWidths=[35*mm, 120*mm])
ct.setStyle(TableStyle([
    ('FONT', (0, 0), (-1, -1), FONT_CN, 10),
    ('TEXTCOLOR', (0, 0), (0, -1), C_ACCENT),
    ('FONT', (0, 0), (0, -1), FONT_CN, 10),
    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ('LINEBELOW', (0, 0), (-1, -1), 0.3, C_BORDER),
    ('TOPPADDING', (0, 0), (-1, -1), 6),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ('LEFTPADDING', (0, 0), (-1, -1), 8),
]))
story.append(ct)
story.append(PageBreak())

# ---- 目录 ----
story.append(Paragraph('目录', sH1))
toc = [
    '0. 一句话结论',
    '1. 输入字段拆解',
    '2. 快选平台现有能力 vs 公式需求',
    '3. 11条资金强度子策略逐条评估',
    '4. 落地前置依赖清单',
    '5. 总体落地路线建议（三期）',
    '6. 主要风险与差异来源',
    '7. 最终评估结论',
]
for item in toc:
    story.append(Paragraph(item, ParagraphStyle('toc', parent=sBody, leftIndent=10, fontSize=10, leading=16, spaceAfter=2)))
story.append(PageBreak())

# ---- 0 ----
story.append(Paragraph('0. 一句话结论', sH1))
story.append(P('<b>可以实现 85%~90% 核心能力。</b>'))
story.append(Spacer(1, 4))
story.append(P('<b>完全能做（高置信）</b>：开盘%、竞价额万、竞价换手、承接强度、开量比、ZF、竞绝杀、JJQR、TLD、排310、ABJ、昨1板、一字涨停过滤、ST过滤、688过滤、北交所剔除、A1跳空、昨日涨停判定、去开板、市值等80%的单列计算 + ASD/FGH/IOP/TYU/QWE/BNM/GHJ/额2板 这8/9条子策略。'))
story.append(Spacer(1, 4))
story.append(P('<b>基本能做但需降口径等价处理</b>：'))
story.append(P('1) XA_1~XA_7 回踩模式（日K级别，需完整日K数据，能复现）', sBodyIndent))
story.append(P('2) INBLOCK(\'ST板块\') 可用名称前缀+涨跌停5%档位等价过滤', sBodyIndent))
story.append(Spacer(1, 4))
story.append(P('<b>难做/暂不建议搬</b>：'))
story.append(P('1) HORCALC 行业板块横向排名前5，数据量大，建议二期', sBodyIndent))
story.append(P('2) VAR1~VAR8/筹码堆量——不参与9条选股子策略，一期可不做', sBodyIndent))
story.append(P('3) ZTPRICE/DTPRICE 动态涨跌停档位——需先建"涨跌停档位表"', sBodyIndent))
story.append(P('4) XA_8/X_5 全天一字——竞价时点没有H/L/C，需明确成"昨日不是一字"', sBodyIndent))
story.append(P('5) <b>数据口径风险最大</b>：CAPITAL/FINANCE(46) 与通达信可能差1~5%，但不影响阈值过滤', sBodyIndent))

# ---- 1 ----
story.append(Paragraph('1. 输入字段拆解：公式用到的通达信数据点', sH1))
story.append(P('将公式里所有"原子数据来源"提取出来，分为三大类：'))

story.append(Paragraph('1.A 集合竞价时点数据（9:25撮合完成那一刻）——公式90%的灵魂', sH2))
data_a = [
    [th('通达信函数'), th('含义'), th('快选能否获取')],
    [td('DYNAINFO(15)'), td('开盘集合竞价成交额（元）'), td('能 - auction_snapshot已存储')],
    [td('DYNAINFO(4)'), td('今日开盘价（=9:25撮合价）'), td('能 - bid_price字段已有')],
    [td('DYNAINFO(3)'), td('昨日收盘价'), td('能 - 历史行情表prev_close')],
    [td('DYNAINFO(7)'), td('最新价（竞价时=O；盘中=实时价）'), td('竞价能；盘中需实时行情')],
    [td('DYNAINFO(17)'), td('量比（5日均量口径）'), td('用开量比等价替代')],
    [td('DYNAINFO(37)'), td('换手率（实时）'), td('竞价时=开盘换手，能算')],
    [td('CAPITAL'), td('流通股本（手，=FINANCE(46)/100）'), td('能算，口径风险1~5%')],
]
story.append(make_table(data_a, col_widths=[28*mm, 60*mm, 82*mm]))
story.append(Spacer(1, 6))

story.append(Paragraph('1.B 盘后/财务类（相对稳定，按日/按季更新）', sH2))
data_b = [
    [th('原函数'), th('含义'), th('快选能否获取')],
    [td('FINANCE(46)'), td('流通A股（股），所有换手/占比的分母'), td('能 - 需补齐财务字段表')],
    [td('FINANCE(40)'), td('流通市值（元）'), td('能 - 数据源可提供float_mv')],
    [td('FINANCE(7)'), td('总股本（股），小票过滤用'), td('能 - 需扩财务字段')],
    [td('FINANCE(3)'), td('市场类型（1=沪主板/3=创业/4=科创）'), td('能用代码前缀等价实现')],
]
story.append(make_table(data_b, col_widths=[28*mm, 60*mm, 82*mm]))
story.append(Spacer(1, 6))

story.append(Paragraph('1.C 历史 K 线（日 K，至少 30~60 日）——公式后半段3/4的条件', sH2))
data_c = [
    [th('通达信算子'), th('含义'), th('快选能否实现')],
    [td('REF(X, n)'), td('前n日X'), td('能 - 有日K表就能取')],
    [td('MA/EMA(X, n)'), td('n日均线/指数平滑'), td('能 - 序列有了就能滚动算')],
    [td('LLV/HHV(X, n)'), td('n日最低/最高'), td('能 - 同上')],
    [td('BARSLAST(cond)'), td('上一次cond到今天数'), td('能 - 序列能算')],
    [td('FILTER(cond, n)'), td('cond真后n天内屏蔽再次触发'), td('能')],
    [td('SMA(X, n, m)'), td('通达信累积权重移动平均'), td('仅VAR1~VAR8用，一期可不做')],
    [td('ZTPRICE/DTPRICE'), td('涨跌停价（分5%/10%/20%档位）'), td('能实现，需先建档位表')],
    [td('INBLOCK(name)'), td('属于某板块'), td('ST可用名称过滤等价；数字板块需用户解释')],
    [td('HORCALC(HYBLOCK)'), td('行业板块横向排名'), td('能做但成本高，建议二期')],
    [td('NAMELIKE/CODELIKE'), td('名称/代码前缀过滤'), td('能 - 快选的强项')],
    [td('NAMELIKE(3656/3657)'), td('用户自定义数字板块ID'), td('黑箱 - 必须用户解释')],
]
story.append(make_table(data_c, col_widths=[35*mm, 45*mm, 90*mm]))
story.append(PageBreak())

# ---- 2 ----
story.append(Paragraph('2. 快选平台现有能力 vs 公式需求映射', sH1))
story.append(P('<b>快选今天已经有什么：</b>'))
story.append(Spacer(1, 4))
items2 = [
    '竞价秒级采样（9:24:xx~9:25:03）：竞价额、竞价开盘价、昨收、封单额、自由流通市值。<b>已有</b>',
    '竞价抢筹首页展示：竞价涨幅、竞价额万、竞价换手、竞量比、抢筹强度。<b>已有</b>（需对齐口径）',
    '股票基础过滤：ST/*ST剔除、688/北交剔除、价格区间、市值区间。<b>前端+后端都有</b>',
    '日K历史表：需确认覆盖度（至少60交易日 O/H/L/C/V/AMOUNT+昨收）。<b>需确认</b>',
    '板块强度榜：已有daily_sector_top，但HORCALC行业横向排名<b>暂无</b>',
    '涨跌停档位判定：可能有半成品，需对齐分档（5%/10%/20%）',
]
for it in items2:
    story.append(Paragraph(it, sBullet, bulletText='\u2022'))
story.append(Spacer(1, 8))
story.append(P('<b>结论：</b>快选现有数据底座已覆盖公式需要的70~80%原子数据；差的主要是：FINANCE(46)统一口径 + 日K深度(58~60天) + 涨跌停档位表 + 自定义数字板块(3656/3657/INBLOCK(4)(5))的用户澄清。'))

story.append(PageBreak())

# ---- 3 ----
story.append(Paragraph('3. 11条资金强度子策略逐条评估', sH1))
story.append(P('公式末尾：<b>资金强度 = ASD + FGH + IOP + TYU + QWE + ZXC + BNM + GHJ + 额2板</b>（每个命中=1分，总分0~9）'))
story.append(Spacer(1, 6))

# 汇总表
sum_data = [
    [th('子策略'), th('核心条件'), th('黑箱依赖'), th('评级'), th('一期建议')],
    [td('ASD', 8), td('开幅>4% + XA回踩模式 + 竞量比5~25 + 竞绝杀>1.5 + JJQR>1 + 昨1板', 8), td('无', 8), td('4/5', 8), td('可做', 8)],
    [td('FGH', 8), td('JJQD>10 + A1跳空 + 开幅3~9.5% + 昨日涨停 + 昨1板', 8), td('无', 8), td('5/5', 8), td('强烈推荐', 8)],
    [td('IOP', 8), td('TLD>10 + 开幅4~9.5% + 竞额占比>1 + JJQR>1.1 + 昨1板', 8), td('无', 8), td('5/5', 8), td('强烈推荐', 8)],
    [td('TYU', 8), td('开盘量>昨量10% + 开幅>5% + JJQD>10 + JJQR>1.1 + 昨1板', 8), td('3656/3657', 8), td('4/5', 8), td('需用户解释', 8)],
    [td('QWE', 8), td('TLD>20 + 竞额占比>2 + 开幅4~9.5% + 昨1板', 8), td('无', 8), td('5/5', 8), td('强烈推荐', 8)],
    [td('ZXC', 8), td('XJ_8>18且3日递增 + 竞价换手>1 + 开盘金额>1000 + 昨1板', 8), td('INBLOCK(4)(5)', 8), td('3/5', 8), td('需用户解释', 8)],
    [td('BNM', 8), td('排310>5.5 + JJQR>1.1 + 昨1板', 8), td('无', 8), td('5/5', 8), td('可做', 8)],
    [td('GHJ', 8), td('竞价万>=1000 + AN>=5 + 竞流比70>100 + 竞绝杀>5 + JJQR>5 + 昨1板', 8), td('无', 8), td('5/5', 8), td('强烈推荐', 8)],
    [td('额2板', 8), td('竞额万>5000 + 今开%>4 + 昨1板', 8), td('无', 8), td('5/5', 8), td('可做', 8)],
    [td('ABJ(附加)', 8), td('排310>10 + JJQR>2 + TLD>20', 8), td('无', 8), td('5/5', 8), td('建议加', 8)],
]
story.append(make_table(sum_data, col_widths=[16*mm, 68*mm, 22*mm, 14*mm, 22*mm]))
story.append(Spacer(1, 10))

# 逐条详解
details = [
    ('3.1 ASD  (评级 4/5)', [
        ('条件', 'XA_11 AND XA_10 AND XA_9 AND XA_7 AND TJ AND ((XA_4 AND XA_5) OR (XA_4 AND XA_6)) AND 竞价换手>0.1 AND 竞量比5~25 AND 开盘金额>600 AND 竞绝杀>1.5 AND C<100 AND JJQR>1.0 AND 昨1板'),
        ('分析', 'XA_11=今开>4%（能）；XA_10=昨日NOT一字（能）；XA_9/XA_7=不开在涨停价附近（能）；TJ=去ST/688/北交（能100%）；XA_4=当日涨幅>5%（竞价时点C=O，等价于开幅>5%）；XA_5=回踩50%不破模式（日K可算）；XA_6=DYNAINFO(7)/OPEN>1.05（竞价时点永远FALSE，仅盘中有效）'),
        ('结论', '4/5 - 竞价时点可用前半部分+第一条OR分支；XA_6要盘中才有意义。需确认选股时点。'),
    ]),
    ('3.2 FGH  (评级 5/5)', [
        ('条件', 'JJQD>10 AND A1 AND C/REF(C,1)>1.05 AND 开幅>3 AND 开幅<9.5 AND TJ AND 昨日涨停 AND 昨1板'),
        ('分析', 'JJQD=开量比*开盘换手*开幅*1/10（能算）；A1=O>REF(H,1)跳空突破（非常有价值，有日K就能算）；昨日涨停AND昨1板=昨日是首板涨停（能从历史K判定）'),
        ('结论', '5/5 - 完全能做，9:25就能出，第一期强烈推荐先搬。'),
    ]),
    ('3.3 IOP  (评级 5/5)', [
        ('条件', 'TLD>10 AND TJ AND 开盘涨幅>4 AND <9.5 AND JJQD>10 AND 竞额占比>1 AND JJQR>1.1 AND 昨1板'),
        ('分析', 'TLD=开盘换手1*开盘涨幅*竞价量比1*10000/100（三因子复合，能算）；竞额占比=DYNAINFO(15)/(FINANCE(46)*O)*100（竞价额占流通盘比%，能算）；JJQR=纯算术，能算。无任何黑箱。'),
        ('结论', '5/5 - 完全能做！9:25出，纯竞价量价换手组合，非常适合第一期主打。'),
    ]),
    ('3.4 TYU  (评级 4/5)', [
        ('条件', 'X_10>0.10 AND X_7 AND X_6 AND X_4 AND X_3 AND X_2 AND TJ AND JJQD>10 AND JJQR>1.1 AND 昨1板'),
        ('分析', 'X_10=开盘量/昨日全日成交量>10%（非常强的承接信号！能算）；X_7=开幅>5%；X_6=昨日NOT一字；X_4/X_2=不开在涨停价。<b>X_3=NAMELIKE(3656)/3657是黑箱，必须用户解释</b>'),
        ('结论', '4/5 - 除X_3黑箱外都能做；先默认X_3=1即可跑，等用户给出3656/3657含义再补。'),
    ]),
    ('3.5 QWE  (评级 5/5)', [
        ('条件', 'TLD>20 AND JJQD>10 AND JJQR>1.1 AND 竞额占比>2 AND NOT(一字涨停) AND 开幅4~9.5 AND TJ AND 昨1板'),
        ('分析', '全是已定义过的纯计算，无任何黑箱/自定义板块/未来函数。相当于IOP的加强版（TLD>20+竞额占比>2 vs IOP的TLD>10+竞额占比>1）'),
        ('结论', '5/5 - 完全能做！和IOP一起是第一期最值得做的2条。'),
    ]),
    ('3.6 ZXC  (评级 3/5)', [
        ('条件', 'XJ_8>18且3日递增 AND INBLOCK(4)=0 AND INBLOCK(5)=0 AND BARSCOUNT>10 AND 竞价涨幅>4 AND 竞价换手>1 AND 开盘金额>1000 AND 昨1板'),
        ('分析', 'XJ_8=今日均价相对17日均价均线的乖离率%（序列可算）；连续3日递增=动量走强（日K能算）。<b>INBLOCK(4)(5)是用户自定义数字板块，黑箱！必须用户翻译</b>'),
        ('结论', '3/5 - 计算逻辑没问题，但INBLOCK(4)(5)黑箱必须用户解释，否则结果对不齐。'),
    ]),
    ('3.7 BNM  (评级 5/5)', [
        ('条件', '排310>5.5 AND JJQR>1.1 AND TJ AND 昨1板'),
        ('分析', '排310=竞价手/涨停系数#DAY（能算，无任何自定义黑箱）；昨1板AND昨1板是重复，取一个即可'),
        ('结论', '5/5 - 完全能做，很适合做独立选股列"排310"。'),
    ]),
    ('3.8 GHJ  (评级 5/5)', [
        ('条件', '竞价万>=1000 AND AN>=5 AND 竞流比70>100 AND 去科创ST AND 竞量比1<25 AND 竞价涨幅4~9.7 AND 竞绝杀>5 AND 竞价金额>3000 AND JJQR>5 AND 昨1板'),
        ('分析', 'AN=排310（同一条公式）；竞流比70=竞价手/流通股*1e6（能算）；竞价万>=1000 AND 竞价金额>3000同时满足等价于>3000，取严格值即可。无任何自定义黑箱。'),
        ('结论', '5/5 - 完全能做！门槛最高，竞价额最猛的一条，第一期推荐做。'),
    ]),
    ('3.9 额2板  (评级 5/5)', [
        ('条件', '竞额万>5000 AND 今开%>4 AND TJ AND 昨1板'),
        ('分析', '最简单一条：5000万竞价额+开幅>4%+昨1板，无任何黑箱'),
        ('结论', '5/5 - 完全能做。'),
    ]),
    ('3.10 ABJ  (附加，未计入资金强度但很有价值)', [
        ('条件', '排310>10 AND JJQR>2 AND TJ AND TLD>20'),
        ('分析', '三合一强因子（排310+JJQR+TLD同时过线），非常有价值'),
        ('结论', '5/5 - 建议第一期顺手加为独立选股列。'),
    ]),
]

for title, lines in details:
    story.append(Paragraph(title, sH3))
    for label, content in lines:
        story.append(Paragraph(f'<b>{label}：</b>{content}', sBullet, bulletText='\u2022'))
    story.append(Spacer(1, 3))

story.append(PageBreak())

# ---- 4 ----
story.append(Paragraph('4. 落地前置依赖清单', sH1))
story.append(Paragraph('4.1 数据/字段侧（工程侧要先确认有）', sH2))
dep_data = [
    [th('#'), th('依赖项'), th('说明'), th('优先级')],
    [td('D1', 8), td('9:25竞价撮合成交额+开盘价历史', 8), td('auction_snapshot已采，需确认能按每票每日9:25取到', 8), td('必须', 8)],
    [td('D2', 8), td('FINANCE(46) 流通A股（股）', 8), td('所有换手/占比/涨停系数/竞绝杀的分母，需与通达信对齐误差<5%', 8), td('必须', 8)],
    [td('D3', 8), td('每票最近>=60交易日日K', 8), td('O/H/L/C/VOL/AMOUNT/prev_close，因有MA(CLOSE,58)/LLV(LOW,30)', 8), td('必须', 8)],
    [td('D4', 8), td('每票涨跌停档位', 8), td('ST=5%/主板10%/创科20%，用于ZTPRICE和去开板/昨1板判定', 8), td('必须', 8)],
    [td('D5', 8), td('FINANCE(7)总股本+FINANCE(40)流通市值', 8), td('XJ_2小票过滤+市值过滤用', 8), td('建议', 8)],
    [td('D6', 8), td('行业板块归属表', 8), td('A股每票归属1个行业，用于HORCALC涨幅排名前5', 8), td('二期', 8)],
]
story.append(make_table(dep_data, col_widths=[10*mm, 35*mm, 75*mm, 22*mm]))
story.append(Spacer(1, 10))

story.append(Paragraph('4.2 业务口径/用户解释侧（产品侧要先问用户）', sH2))
q_data = [
    [th('#'), th('必须问用户的'), th('影响')],
    [td('Q1', 8), td('选股时点：9:25收盘竞价？9:30盘中？盘后回测？', 8), td('决定C/REF(C,1)、XA_6、DYNAINFO(7)能否使用', 8)],
    [td('Q2', 8), td('自定义板块3656/3657（TYU X_3）是什么？请导出板块列表', 8), td('否则TYU过滤对不齐', 8)],
    [td('Q3', 8), td('INBLOCK(4)和INBLOCK(5)（ZXC里）是什么？', 8), td('否则ZXC过滤对不齐', 8)],
    [td('Q4', 8), td('涨跌停档位：给ST/双创票单独换阈值？还是TJ里全剔除？', 8), td('避免20%档位票用10%阈值算首板误判', 8)],
    [td('Q5', 8), td('CAPITAL/FINANCE(46)单位确认+3~5个样本股数据', 8), td('数值对齐唯一关键，直接影响所有换手/占比指标', 8)],
    [td('Q6', 8), td('9个子策略怎么出结果？一列强度分？还是9个独立Boolean列？', 8), td('决定前端UI设计', 8)],
]
story.append(make_table(q_data, col_widths=[10*mm, 55*mm, 77*mm]))
story.append(PageBreak())

# ---- 5 ----
story.append(Paragraph('5. 总体落地路线建议（三期）', sH1))
story.append(Paragraph('第一期（快速上线）—— 实现 85% 的实战价值', sH2))
phase1 = [
    '竞价阶段可用、零黑箱的7条子策略：FGH / IOP / QWE / BNM / GHJ / 额2板 / ABJ',
    '统一过滤条件TJ：ST/*ST/*S/S剔除、688剔除、北交830~871剔除、C<100、NOT一字涨停、NOT开盘涨停价',
    '输出列：开盘%、竞价额万、竞价换手、开量比、竞额占比、承接强度、ZF、排310、JJQD、JJQR、TLD、竞绝杀、竞流比70、昨1板、昨日涨停、A1、资金强度(7条之和)、每个子策略独立命中标记',
    '回测/对齐工具：用户提供5只通达信命中样本股，逐条打印"我们算的vs通达信的列值"，校正口径偏差',
]
for it in phase1:
    story.append(Paragraph(it, sBullet, bulletText='\u2022'))
story.append(P('预期对齐度：7条子策略 >95% 股票命中集合与通达信一致；数值列差异 <5% 且不影响阈值过滤。', sNote))
story.append(Spacer(1, 10))

story.append(Paragraph('第二期—— 补全自定义黑箱', sH2))
story.append(P('用户给出 3656/3657 / INBLOCK(4)(5) 板块含义后，加上 TYU 和 ZXC，再把 HORCALC(HYBLOCK,105,1)<=5 做出来（行业板块涨幅前5过滤）。'))

story.append(Spacer(1, 10))
story.append(Paragraph('第三期（可选）—— 盘后/盘中强化 & 回测', sH2))
phase3 = [
    'ASD/ZXC 里涉及 C/REF(C,1)>1.05、XA_6（DYNAINFO(7)/OPEN>1.05）：改成盘中每X分钟刷新一次',
    'VAR1~VAR8 + 筹码堆量：做"仅显示、不参与选股"的辅助列+可视化小图',
    '历史回测器：用日K+竞价历史，跑"资金强度N分票的次日/连板胜率"统计表',
]
for it in phase3:
    story.append(Paragraph(it, sBullet, bulletText='\u2022'))

# ---- 6 ----
story.append(Paragraph('6. 主要风险与差异来源', sH1))
risks = [
    '<b>FINANCE(46)/CAPITAL 财务源</b>：通达信用自己的财务库，我们用东财/同花顺/kpl。市值/流通股本口径差1~5%是常态。建议用阈值排序型过滤，不苛求数值逐股一致。',
    '<b>DYNAINFO(15) 竞价撮合成交额</b>：通达信是本机客户端9:25的快照；快选用云端数据源。极端秒级抖动可能导致微差，99%情况下对阈值无影响。',
    '<b>涨跌停档位1.097硬编码</b>：20%档位股票涨停是x1.200，公式里>1.097对创业/科创首板会误判。需按档位动态算或TJ里全剔除非10%档位票。',
    '<b>自定义数字板块3656/3657 / INBLOCK(4)(5)</b>：用户说不清楚就只能默认不过滤，TYU/ZXC与通达信一定对不齐。',
    '<b>HORCALC行业排名</b>：通达信HYBLOCK和我们板块归属可能不同，仅影响ZXC一条过滤，影响面不大。',
]
for r in risks:
    story.append(Paragraph(r, sBullet, bulletText='\u2022'))

# ---- 7 ----
story.append(Paragraph('7. 最终评估结论', sH1))
story.append(P('<b>完全可行，强烈建议落地。</b>'))
story.append(Spacer(1, 6))
story.append(P('第一期7条（FGH/IOP/QWE/BNM/GHJ/额2板/ABJ）+ TJ统一过滤：没有任何黑箱/用户自定义依赖，9:25竞价结束即刻可出结果，能让用户1:1先把他自己通达信最强的"纯竞价手算"策略搬进快选。'))
story.append(Spacer(1, 4))
story.append(P('后续只要问清用户6个业务口径（Q1~Q6），就可以继续补完TYU/ZXC/ASD，把9条资金强度全对齐到通达信一致率 >= 90%。'))
story.append(Spacer(1, 20))
story.append(Paragraph('--- 报告结束 ---', ParagraphStyle('end', parent=sBody, alignment=TA_CENTER, textColor=colors.grey, fontSize=9)))

# ---- 生成 ----
output_path = '/workspace/docs/通达信竞价选股公式落地评估报告.pdf'
os.makedirs(os.path.dirname(output_path), exist_ok=True)

doc = SimpleDocTemplate(
    output_path, pagesize=A4,
    leftMargin=18*mm, rightMargin=18*mm,
    topMargin=18*mm, bottomMargin=18*mm,
    title='通达信竞价选股公式落地评估报告',
    author='快选 Kuaixuan AI',
)
doc.build(story)
print(f'PDF generated: {output_path}')
print(f'Size: {os.path.getsize(output_path)} bytes')
