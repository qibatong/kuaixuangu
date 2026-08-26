# -*- coding: utf-8 -*-
"""
连板天梯 PNG 图片生成
====================
把连板梯队数据 {1:[...],2:[...],...} 渲染成一张「连板天梯」竖版图片:
  - 顶部: 标题 + 日期 + 市场汇总(总涨停/最高连板)
  - 中部: 统计阶梯(首板~五板+ 横向条形, 宽度=该档次占比, 呈梯级)
  - 下部: 逐股清单(按档次分组表格: 代码/名称/涨停原因/连板/首板时间/封单/换手/板块)

用于每日盘后自动生成、App 内查看与导出图片。附开源中文字体
app/assets/fonts/wqy-microhei.ttc 保证中文渲染。
"""
import os
import threading

from PIL import Image, ImageDraw, ImageFont

from ..core import config, logger

log = logger.get_logger(__name__)

# ---------- 外观常量 ----------
WIDTH = 960
MARGIN = 26
BG = (255, 255, 255)            # 白底, 便于导出分享
INK = (30, 32, 40)              # 主文字
MUTED = (130, 136, 150)         # 次要文字
UP = (224, 57, 51)              # 涨停红
RED_BG = (252, 233, 231)        # 浅红背景
LINE = (235, 237, 242)          # 分隔线
BAND_BG = (248, 242, 240)       # 统计阶梯底色

FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "fonts", "wqy-microhei.ttc",
)

# 档次标签与标题色阶(由低到高, 板数越高颜色越深)
TIERS = [1, 2, 3, 4, 5]
TIER_LABEL = {1: "首 板", 2: "二 板", 3: "三 板", 4: "四 板", 5: "五板+"}

_font_cache = {}


def _font(size):
    """获取指定字号的中文字体(带缓存)"""
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    return _font_cache[size]


# ---------- 数据规整 ----------
def _collect(data):
    """把 {pid: [stocks]} 规整成按档次排序的阶梯数据 + 逐股清单。
    返回 ({tier_label: count, ...} 有序, 总条数, rows)"""
    tiers, total, rows = [], 0, []
    for pid in TIERS:
        lst = data.get(pid) or []
        count = len(lst)
        total += count
        clean = []
        for it in lst:
            try:
                clean.append({
                    "code": str(it.get("code", "")),
                    "name": str(it.get("name", "")),
                    "reason": str(it.get("reason", "") or ""),
                    "board": str(it.get("boardName", "") or it.get("board", "") or ""),
                    "zt": int(it.get("ztCount", 0) or 0),
                    "limitTime": (it.get("limitTime") or 0),
                    "seal": float(it.get("seal", 0) or 0),
                    "turnover": float(it.get("turnover", 0) or 0),
                    "pid": pid,
                })
            except (TypeError, ValueError):
                continue
        tiers.append({"label": TIER_LABEL[pid], "count": count})
        rows.append(clean)
    return tiers, total, rows


def _truncate(text, width_px, size):
    """按像素宽度截断文本(中文为双宽), 末尾加省略号"""
    text = (text or "").strip().replace("\n", " ").replace("\r", " ")
    font = _font(size)
    if font.getbbox(text)[2] <= width_px:
        return text
    res = ""
    for ch in text:
        if font.getbbox(res + ch)[2] > width_px - 14:
            break
        res += ch
    return res + "…"


def _fmt_amount(seal):
    """封单/成交额: 元 -> 亿/万"""
    if seal >= 1e8:
        return f"{seal / 1e8:.2f}亿"
    if seal >= 1e4:
        return f"{seal / 1e4:.0f}万"
    return f"{seal:.0f}"


def _max_connect(tiers, rows):
    """最高连板数"""
    mx = 0
    for lst in rows:
        for it in lst:
            mx = max(mx, it["zt"])
    if mx == 0:
        mx = max((t["count"] for t in tiers), default=0) and len(tiers)
    return mx


# ---------- 渲染 ----------
def build_png(data, date_str, out_path):
    """
    渲染连板天梯 PNG 并写出到 out_path。
    data: {1:[...],2:[...],3:[...],4:[...],5:[...]} 连板梯队
    date_str: 'YYYY-MM-DD'
    """
    tiers, total, rows = _collect(data)
    count_map = {t["label"]: t["count"] for t in tiers}

    # ---------- 预计算高度 ----------
    head_h = 118
    summary_h = 78
    band_h = 62                            # 每个档次的统计阶梯带
    detail_head_h = 34                     # 逐股区"列头"
    rows_start = head_h + summary_h + band_h * len(tiers)

    # 明细区: 每个档次一 "组" = 组头(档名+数量) + 列头 + 每行
    groups = []                            # (组头高, 列头行数, 数据行高)
    group_heights = []
    row_h = 30
    for lst in rows:
        n = len(lst)
        col_lines = 1                       # 列头单行(首板档说明可两行)
        head_extra = 0
        group_heights.append(34 + col_lines * 30 + n * row_h + 8)
    list_total_h = sum(group_heights)
    bottom_pad = 40
    total_h = rows_start + list_total_h + bottom_pad

    img = Image.new("RGB", (WIDTH, total_h), BG)
    draw = ImageDraw.Draw(img)

    y = 0
    # ---------- 顶部标题 ----------
    draw.rectangle([0, 0, WIDTH, head_h], fill=(26, 28, 36))
    title_font = _font(34)
    draw.text((MARGIN, 22), "连板天梯", font=title_font, fill=(255, 210, 90))
    sub = _font(17)
    src = _font(14)
    draw.text((MARGIN + 205, 66), "快选 · 连板梯队统计", font=src, fill=(150, 158, 180))
    date_font = _font(26)
    d_text = date_str
    d_w = date_font.getbbox(d_text)[2]
    draw.text((WIDTH - MARGIN - d_w, 30), d_text, font=date_font, fill=(255, 255, 255))
    wk = ["一", "二", "三", "四", "五", "六", "日"]
    try:
        import datetime as _dt
        dt = _dt.datetime.strptime(date_str, "%Y-%m-%d")
        wd = wk[dt.weekday()]
        draw.text((WIDTH - MARGIN - d_w, 70), f"周{wd}", font=_font(15),
                  fill=(190, 196, 210))
    except Exception:
        pass

    # ---------- 汇总 ----------
    y = head_h
    draw.rectangle([0, y, WIDTH, y + summary_h], fill=(250, 250, 252))
    draw.line([MARGIN, y + summary_h, WIDTH - MARGIN, y + summary_h], fill=LINE)
    big = _font(34)
    mid = _font(16)
    # 总涨停
    draw.text((MARGIN, y + 12), str(total), font=big, fill=UP)
    draw.text((MARGIN, y + 52), "涨停总数(家)", font=mid, fill=MUTED)
    # 最高连板
    mx = _max_connect(tiers, rows)
    c2 = MARGIN + 180
    draw.text((c2, y + 12), str(mx), font=big, fill=UP)
    draw.text((c2, y + 52), "最高连板(板)", font=mid, fill=MUTED)
    # 各档家数
    x = c2 + 180
    for label, cnt in count_map.items():
        draw.text((x, y + 16), f"{label}  {cnt}家", font=_font(17), fill=INK)
        draw.text((x, y + 50), label.replace(" ", ""), font=_font(12), fill=MUTED)
        x += 128
    y += summary_h

    # ---------- 统计阶梯 ----------
    max_count = max([t["count"] for t in tiers] + [1])
    bar_area_w = WIDTH - 2 * MARGIN - 150   # 留 150 给右侧计数
    for t in tiers:
        draw.rectangle([0, y, WIDTH, y + band_h], fill=BAND_BG)
        draw.line([MARGIN, y + band_h, WIDTH - MARGIN, y + band_h], fill=LINE)
        # 档名
        draw.text((MARGIN, y + 18), t["label"], font=_font(24), fill=INK)
        # 阶梯条(宽度比例 = count/max), 由低到高递增呈现梯级
        bw = int(bar_area_w * (t["count"] / max_count))
        bw = max(bw, 8)
        bx = MARGIN
        by = y + 16
        draw.rounded_rectangle([bx, by, bx + bw, by + 30], radius=6, fill=UP)
        # 计数
        m = _font(22)
        cnt_text = f"{t['count']} 家"
        draw.text((bx + bw + 14, y + 15), cnt_text, font=m, fill=UP)
        y += band_h

    # ---------- 逐股清单 ----------
    rows_start = y
    for idx, (lst, gh) in enumerate(zip(rows, group_heights)):
        g_h = gh
        # 组头
        draw.rectangle([0, y, WIDTH, y + 34], fill=(245, 214, 210))
        draw.text((MARGIN, y + 6), f'{tiers[idx]["label"]}  {len(lst)} 家',
                  font=_font(19), fill=UP)
        y += 34
        # 列头
        colh_font = _font(15)
        draw.text((MARGIN, y + 7), "代码", font=colh_font, fill=MUTED)
        draw.text((MARGIN + 110, y + 7), "名称", font=colh_font, fill=MUTED)
        draw.text((MARGIN + 220, y + 7), "涨停原因 / 所属板块", font=colh_font, fill=MUTED)
        draw.text((MARGIN + 600, y + 7), "连板", font=colh_font, fill=MUTED)
        draw.text((MARGIN + 660, y + 7), "首板", font=colh_font, fill=MUTED)
        draw.text((MARGIN + 740, y + 7), "封单", font=colh_font, fill=MUTED)
        draw.text((MARGIN + 840, y + 7), "换手", font=colh_font, fill=MUTED)
        y += 30
        # 数据行
        row_font = _font(16)
        for it in lst:
            draw.text((MARGIN, y + 5), it["code"], font=row_font, fill=INK)
            draw.text((MARGIN + 110, y + 5), _truncate(it["name"], 96, 16),
                      font=row_font, fill=INK)
            reason = it["reason"] or it["board"]
            reason_disp = _truncate(reason, 360, 16) if reason else "—"
            draw.text((MARGIN + 220, y + 5), reason_disp, font=row_font, fill=MUTED)
            draw.text((MARGIN + 600, y + 5), str(it["zt"]) + "板", font=row_font, fill=UP)
            lt = it["limitTime"]
            lts = ("%02d:%02d" % (lt // 3600 % 24, lt // 60 % 60)) if lt else "—"
            draw.text((MARGIN + 660, y + 5), lts, font=row_font, fill=INK)
            draw.text((MARGIN + 740, y + 5), _fmt_amount(it["seal"]), font=row_font, fill=INK)
            draw.text((MARGIN + 840, y + 5), f"{it['turnover']:.1f}%", font=row_font, fill=INK)
            y += row_h
        # 组间分隔
        draw.line([MARGIN, y, WIDTH - MARGIN, y], fill=LINE)
        y += 8

    # 底部水印
    draw.text((MARGIN, total_h - 30), "快选 Kuaixuan · 连板天梯（每日盘后自动生成）",
              font=_font(13), fill=MUTED)

    # ---------- 写出 ----------
    _lock = threading.Lock()
    with _lock:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path, "PNG")
    return out_path


def generate_for_date(date_str):
    """
    生成指定日期的连板天梯 PNG(数据来自 ladder_history 落库)。
    返回生成的图片绝对路径; 无数据返回 None。
    """
    try:
        from . import kpl
        data = kpl.query_ladder_history(date_str)
        total = sum(len(v or []) for v in data.values())
        if total == 0:
            return None
        os.makedirs(config.LADDER_IMG_DIR, exist_ok=True)
        out_path = os.path.join(config.LADDER_IMG_DIR, f"{date_str}.png")
        return build_png(data, date_str, out_path)
    except Exception as e:
        log.error("连板天梯图片生成失败 date=%s err=%s", date_str, e)
        return None