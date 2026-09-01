# -*- coding: utf-8 -*-
"""
连板天梯 PNG 图片生成
====================
把连板梯队数据 {1:[...],2:[...],...} 渲染成一张「连板天梯」竖版图片:
  - 顶部: 标题 + 日期 + 市场汇总(涨停家数 / 最高连板 / 各档分布)
  - 中部: 统计阶梯(按档位的阶梯条, 高板色更深)
  - 下部: 连板池逐股清单(高板在上, 按档分组, 列: 代码/名称/连板/涨停概念/首板/封单/换手)

数据口径(重要, 已修正 2026-08-26):
  - 连板数: 用分组档位 pid(=ladder), 而非不可靠的 ztCount 字段
  - 首板时间: limitTime 为完整 epoch 秒, 转北京时间 HH:MM
  - 涨停概念: reason 常为空, 回落 concept(板块概念) 再 boardName

用于每日盘后自动生成、App 内查看与导出图片。内置开源中文字体
app/assets/fonts/wqy-microhei.ttc 保证中文渲染。
"""
import datetime
import os
import threading

from PIL import Image, ImageDraw, ImageFont

from ..core import config, logger

log = logger.get_logger(__name__)

# ---------- 外观常量 ----------
WIDTH = 900
MARGIN = 24
BG = (255, 255, 255)             # 白底, 便于导出分享
NAVY = (22, 26, 36)              # 顶部深色
GOLD = (255, 206, 94)            # 标题金色
INK = (40, 42, 50)               # 主文字
MUTED = (128, 134, 148)          # 次要文字
FAINT = (176, 182, 194)          # 弱文字
UP = (222, 54, 58)               # 涨停红
LINE = (236, 238, 243)           # 分隔线
ZEBRA = (250, 251, 253)          # 斑马行
COLH_BG = (247, 247, 250)        # 列头底
WHITE = (255, 255, 255)

# 档位色阶(板数越高越深)
TIER_COLOR = {
    1: (228, 66, 70),
    2: (212, 48, 62),
    3: (196, 35, 62),
    4: (180, 27, 62),
    5: (163, 18, 62),
    6: (146, 10, 62),
    7: (128, 8, 58),
    8: (110, 6, 54),
}

FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "fonts", "wqy-microhei.ttc",
)

# 高度板扩展到 8 板(2026-09-01 主人需求): 开盘啦 pid 只到 5, 真实连板数由东财
# limitUpDays 注入, 按真实板数重分 1~8 档, ≥8 板归入「八板+」
TIERS = [1, 2, 3, 4, 5, 6, 7, 8]
TIER_LABEL = {1: "首板", 2: "二板", 3: "三板", 4: "四板", 5: "五板",
              6: "六板", 7: "七板", 8: "八板+"}
MAX_TIER = 8

# 断板反包区配色(暖橙, 与连板红区分)
FB_BG = (255, 248, 243)
FB_HEAD = (255, 138, 62)
FB_INK = (160, 74, 28)

_font_cache = {}


def _font(size):
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    return _font_cache[size]


# 列布局: (列名, 左缘x, 宽度px, 对齐)  右侧时间/封单/换手右对齐更美观
COLS = [
    ("代码",   24,  96,  "l"),
    ("名称",   120, 118, "l"),
    ("连板",   238, 60,  "l"),
    ("涨停概念", 298, 336, "l"),
    ("首板",   634, 64,  "r"),
    ("封单",   698, 96,  "r"),
    ("换手",   794, 82,  "r"),
]


# ---------- 数据规整 ----------
def _collect(data):
    """按真实连板数(zt=max(pid, limitUpDays))重分 1~8 档;
    返回 (tiers, total, rows): tiers=8 档全量统计(含空档, 供阶梯条/chips),
    rows=[(tier_dict, 股票列表), ...] 只含非空档且高板在前(供连板池渲染)"""
    grouped = {t: [] for t in TIERS}
    total = 0
    for pid in (1, 2, 3, 4, 5):
        lst = data.get(pid) or []
        for it in lst:
            try:
                concept = str(it.get("concept", "") or "").strip().replace("\n", " ")
                reason = str(it.get("reason", "") or "").strip()
                board = str(it.get("boardName", "") or it.get("board", "") or "").strip()
                if not reason and concept:
                    reason = concept
                elif not reason:
                    reason = board
                # 真实连板数: 优先用当日涨停池注入的真实连板数(解决五板+里 6~8 板区分);
                # 否则回退档位 pid
                zt = max(int(pid), int(it.get("limitUpDays") or pid or 0))
                tier = min(zt, MAX_TIER)
                grouped[tier].append({
                    "code": str(it.get("code", "")),
                    "name": str(it.get("name", "")),
                    "zt": zt,
                    "reason": reason,
                    "limitTime": (it.get("limitTime") or 0),
                    "seal": float(it.get("seal", 0) or 0),
                    "turnover": float(it.get("turnover", 0) or 0),
                })
            except (TypeError, ValueError):
                continue
    tiers = []
    rows = []
    for t in TIERS:
        clean = grouped[t]
        total += len(clean)
        tier = {"label": TIER_LABEL[t], "count": len(clean), "pid": t}
        tiers.append(tier)
        if clean:
            rows.append((tier, clean))
    rows.reverse()   # 高板在前
    return tiers, total, rows


def _truncate(text, width_px, size):
    text = (text or "").replace("\n", " ").replace("\r", " ").strip()
    if not text:
        return "—"
    font = _font(size)
    if font.getbbox(text)[2] <= width_px:
        return text
    res = ""
    for ch in text:
        if font.getbbox(res + ch)[2] > width_px - 13:
            break
        res += ch
    return res + "…"


def _fmt_amount(seal):
    if seal >= 1e8:
        return f"{seal / 1e8:.2f}亿"
    if seal >= 1e4:
        return f"{seal / 1e4:.0f}万"
    return f"{seal:.0f}"


def _fmt_limit(epoch):
    """epoch 秒 -> 北京时间 HH:MM"""
    if not epoch:
        return "—"
    try:
        dt = datetime.datetime.fromtimestamp(
            int(epoch), tz=datetime.timezone(datetime.timedelta(hours=8)))
        return dt.strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


def _baseline(text, font):
    """返回文本高度, 用于垂直居中"""
    bbox = font.getbbox(text)
    return bbox[1]


# ---------- 渲染 ----------
def build_png(data, date_str, out_path, fanbao=None):
    """渲染连板天梯 PNG。fanbao: 断板反包股列表 [{code,name,reason,change}, ...],
    非空时在连板池下方渲染「断板反包」区(暖橙配色, 复盘一眼识别)。"""
    tiers, total, rows = _collect(data)
    fanbao = fanbao or []

    # ---------- 预计算结构/高度 ----------
    header_h = 118
    summary_h = 134                     # 卡片 + 两行 chips(8 档)
    stats_h = 250                       # 8 档阶梯条(46 + 8*24 + 余量)
    detail_pad = 14                     # 连板池内边距
    tier_bar_h = 46
    colh_h = 34
    row_h = 44
    fb_head_h = 40                      # 断板反包标题栏
    fb_row_h = 36                       # 断板反包行高

    sections_h = 0
    for _tier, lst in rows:
        sections_h += (tier_bar_h + colh_h + max(len(lst), 1) * row_h + detail_pad)
    fanbao_h = (fb_head_h + len(fanbao) * fb_row_h + 16) if fanbao else 0

    bottom_pad = 44
    total_h = header_h + summary_h + stats_h + int(sections_h) + fanbao_h + bottom_pad

    img = Image.new("RGB", (WIDTH, total_h), BG)
    draw = ImageDraw.Draw(img)

    y = 0

    # ---------- 顶部标题 ----------
    draw.rectangle([0, 0, WIDTH, header_h], fill=NAVY)
    draw.text((MARGIN, 26), "连板天梯", font=_font(32), fill=GOLD)
    draw.text((MARGIN, 79), "涨停梯队日报 · 快选Kuaixuan", font=_font(15), fill=(156, 164, 184))
    # 右上: 日期 + 星期
    date_font = _font(22)
    dw = date_font.getbbox(date_str)[2]
    dx = WIDTH - MARGIN - dw
    draw.text((dx, 30), date_str, font=date_font, fill=WHITE)
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        wk = ["一", "二", "三", "四", "五", "六", "日"][dt.weekday()]
    except Exception:
        wk = ""
    if wk:
        d2 = date_font.getbbox(f"周{wk}")[2]
        draw.text((WIDTH - MARGIN - d2, 80), f"周{wk}", font=_font(15), fill=(190, 196, 210))
    # 头部下缘金色装饰线
    draw.line([MARGIN, header_h - 3, WIDTH - MARGIN, header_h - 3], fill=GOLD, width=2)
    y = header_h

    # ---------- 汇总指标 ----------
    draw.rectangle([0, y, WIDTH, y + summary_h], fill=(249, 249, 252))
    draw.line([0, y + summary_h, WIDTH, y + summary_h], fill=LINE)
    big = _font(30)
    lab_font = _font(14)
    # 最高连板 = 全部个股真实连板数最大值(支持五板+里的 6~8 板等真实高度)
    mx = max((r["zt"] for _t, lst in rows for r in lst), default=1)
    cards = [
        ("涨停家数", str(total), "家"),
        ("最高连板", str(mx), "板"),
    ]
    cx = MARGIN
    for cap, val, unit in cards:
        draw.text((cx, y + 18), val, font=big, fill=UP)
        vw = big.getbbox(val)[2]
        draw.text((cx + vw + 6, y + 28), unit, font=_font(16), fill=MUTED)
        draw.text((cx, y + 60), cap, font=lab_font, fill=MUTED)
        cx += 140
    # 档位分布 chips(8 档两行: 1~4 一行, 5~8 一行; 空档也显示 count=0 以暴露断层)
    chip_font = _font(14)
    cnt_font = _font(16)
    for row_i, t_slice in enumerate((tiers[:4], tiers[4:])):
        xc = MARGIN
        for t in t_slice:
            cw = chip_font.getbbox(t["label"])[2] + 34
            ch = 24
            cy = y + (72 if row_i == 0 else 104)
            draw.rounded_rectangle([xc, cy, xc + cw, cy + ch], radius=ch // 2,
                                   fill=(255, 236, 236), outline=(255, 214, 214))
            draw.text((xc + 12, cy + 4), t["label"], font=chip_font, fill=UP)
            n_txt = str(t["count"])
            nw = cnt_font.getbbox(n_txt)[2]
            draw.text((xc + cw - 7 - nw, cy + 4), n_txt, font=cnt_font, fill=UP)
            xc += cw + 10
    y += summary_h

    # ---------- 统计天梯(阶梯条, 8 档低→高, 高板色更深, 空档画短条暴露断层) ----------
    draw.rectangle([0, y, WIDTH, y + stats_h], fill=WHITE)
    draw.text((MARGIN, y + 14), "连板分布", font=_font(17), fill=INK)
    max_count = max([t["count"] for t in tiers] + [1])
    bar_area_w = WIDTH - 2 * MARGIN - 60
    bx = MARGIN + 60
    for t in tiers:
        by0 = y + 46 + (TIERS.index(t["pid"])) * 24
        bw = int(bar_area_w * (t["count"] / max_count)) if t["count"] else 4
        bw = max(bw, 4)
        color = TIER_COLOR[t["pid"]]
        draw.rounded_rectangle([bx, by0, bx + bw, by0 + 18], radius=5, fill=color)
        draw.text((MARGIN, by0 - 2), t["label"], font=_font(14), fill=INK)
        c_t = f"{t['count']} 家"
        c_font = _font(14)
        draw.text((bx + bw + 8, by0 - 2), c_t, font=c_font, fill=MUTED)
    y += stats_h

    # ---------- 连板池逐股清单(高板在上, 只渲染非空档) ----------
    draw.text((MARGIN, y + 4), "连板池", font=_font(17), fill=INK)
    y += 34

    for tier, lst in rows:
        color = TIER_COLOR[tier["pid"]]
        # 档位头
        draw.rounded_rectangle([MARGIN, y, WIDTH - MARGIN, y + tier_bar_h],
                               radius=8, fill=color)
        draw.text((MARGIN + 16, y + 8), tier["label"], font=_font(22), fill=WHITE)
        n_t = f"{len(lst)} 只"
        n_font = _font(17)
        nw = n_font.getbbox(n_t)[2]
        draw.text((WIDTH - MARGIN - 16 - nw, y + 12), n_t, font=n_font, fill=(255, 230, 230))
        y += tier_bar_h
        # 列头
        draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + colh_h], fill=COLH_BG)
        colfh = _font(15)
        draw.line([MARGIN, y + colh_h, WIDTH - MARGIN, y + colh_h], fill=LINE)
        for cname, x0, cw, al in COLS:
            if al == "r":
                tx = x0 + cw - colfh.getbbox(cname)[2]
            else:
                tx = x0
            draw.text((tx, y + 9), cname, font=colfh, fill=MUTED)
        y += colh_h

        row_font = _font(16)
        name_font = _font(16)
        for r_i, it in enumerate(lst):
            row_bg = WHITE if r_i % 2 == 0 else ZEBRA
            draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + row_h], fill=row_bg)
            # 代码
            draw.text((COLS[0][1], y + 13), it["code"], font=_font(18), fill=INK)
            # 名称
            draw.text((COLS[1][1], y + 13), _truncate(it["name"], COLS[1][2], 16),
                      font=name_font, fill=INK)
            # 连板
            zt_t = f"{it['zt']}板"
            zt_font = _font(15)
            draw.rounded_rectangle([COLS[2][1], y + 12, COLS[2][1] + 44, y + 32],
                                   radius=10, fill=(255, 236, 236), outline=(255, 210, 210))
            draw.text((COLS[2][1] + 9, y + 13), zt_t, font=zt_font, fill=UP)
            # 涨停概念
            draw.text((COLS[3][1], y + 13), _truncate(it["reason"], COLS[3][2], 16),
                      font=row_font, fill=MUTED)
            # 首板/封单/换手 (右对齐)
            cells = [
                (4, _fmt_limit(it["limitTime"]), INK),
                (5, _fmt_amount(it["seal"]), INK),
                (6, f"{it['turnover']:.1f}%", MUTED),
            ]
            f_d = row_font
            for cidx, val, colo in cells:
                cname, x0, cw, al = COLS[cidx]
                vw = f_d.getbbox(val)[2]
                draw.text((x0 + cw - vw, y + 13), val, font=f_d, fill=colo)
            y += row_h
        # 组间分隔
        y += 6

    # ---------- 断板反包(暖橙区) ----------
    if fanbao:
        y += 8
        draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + fb_head_h],
                       fill=FB_HEAD)
        draw.text((MARGIN + 14, y + 9), "断板反包", font=_font(19), fill=WHITE)
        sub = f"今日重新涨停 · 昨日断板 · 近5日内曾涨停 · {len(fanbao)} 只"
        sf = _font(14)
        sw = sf.getbbox(sub)[2]
        draw.text((WIDTH - MARGIN - 14 - sw, y + 11), sub, font=sf, fill=(255, 236, 220))
        y += fb_head_h
        # 列头
        fb_colh = 30
        draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + fb_colh], fill=(255, 245, 238))
        for cname, x0, cw, al in [
                ("代码", 24, 96, "l"), ("名称", 120, 118, "l"),
                ("涨停原因", 238, 430, "l"), ("涨幅", 668, 90, "r")]:
            fh = _font(14)
            if al == "r":
                tx = x0 + cw - fh.getbbox(cname)[2]
            else:
                tx = x0
            draw.text((tx, y + 7), cname, font=fh, fill=(180, 100, 60))
        draw.line([MARGIN, y + fb_colh, WIDTH - MARGIN, y + fb_colh], fill=(255, 224, 200))
        y += fb_colh
        # 行
        fb_row_bg = [WHITE, FB_BG]
        for i, it in enumerate(fanbao):
            draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + fb_row_h],
                           fill=fb_row_bg[i % 2])
            draw.text((24, y + 9), it["code"], font=_font(17), fill=FB_INK)
            draw.text((120, y + 9), _truncate(it["name"], 118, 16), font=_font(16), fill=INK)
            draw.text((238, y + 9), _truncate(it["reason"], 430, 15), font=_font(15), fill=MUTED)
            chg_t = f"+{it['change']:.1f}%"
            cf = _font(16)
            cwv = cf.getbbox(chg_t)[2]
            draw.text((668 + 90 - cwv, y + 9), chg_t, font=cf, fill=FB_HEAD)
            y += fb_row_h
        y += 10

    # ---------- 底部 ----------
    y += 10
    draw.line([MARGIN, total_h - 34, WIDTH - MARGIN, total_h - 34], fill=LINE)
    draw.text((WIDTH - MARGIN - 180, total_h - 30), "快选 Kuaixuan",
              font=_font(13), fill=FAINT)

    # ---------- 写出 ----------
    _lock = threading.Lock()
    with _lock:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path, "PNG")
    return out_path


def generate_for_date(date_str):
    try:
        from . import kpl
        data = kpl.query_ladder_history(date_str)
        total = sum(len(v or []) for v in data.values())
        if total == 0:
            return None
        # 注入当日涨停池的真实连板数, 修正五板+里 6~8 板以上显示与顶部最高连板
        real_lb = kpl.real_limit_days(date_str)
        if real_lb:
            for pid, lst in data.items():
                for it in lst:
                    code = str(it.get("code", ""))
                    if code in real_lb:
                        it["limitUpDays"] = real_lb[code]
        # 断板反包检测(近5日内曾涨停 + 昨日断板 + 今日重新涨停)
        fanbao = kpl.fetch_fanbao_stocks(date_str)
        os.makedirs(config.LADDER_IMG_DIR, exist_ok=True)
        out_path = os.path.join(config.LADDER_IMG_DIR, f"{date_str}.png")
        return build_png(data, date_str, out_path, fanbao=fanbao)
    except Exception as e:
        log.error("连板天梯图片生成失败 date=%s err=%s", date_str, e)
        return None