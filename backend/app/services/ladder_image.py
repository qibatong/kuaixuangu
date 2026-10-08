# -*- coding: utf-8 -*-
"""
连板天梯 PNG 图片生成(浅色复盘海报风, 2026-09-21 重设计 v2)
======================================================
参考开盘啦「连板天梯」骨架 + 自有改良(主人 2026-09-21 拍板"按你的思路做"):
  - 整体浅色: 页面浅灰底 + 白色主卡片, 梯队斑马纹分区
  - 头部: 白底「N月N日 连板天梯」大标题 + 风险提示
  - 题材热榜 chips: 热度分档配色(越热越红: ≥8家深红实底 / ≥4家红实底 / 其余粉底红字)
  - 主体: 蓝色横幅 + 按档位分层(高板在上), 左侧档位标尺(档名+n家), 右侧卡片流
          · 高板(≥4板)大卡片: 名称/涨停时间/代码/N连板红标签/封单/题材
          · 低板(≤3板)小卡片: 名称/涨停时间/N连板红标签/题材
          · 首板卡片带「反包」橙色小标签(断板反包)
数据口径(重要, 已修正 2026-08-26):
  - 连板数: 用分组档位 pid(=ladder), 而非不可靠的 ztCount 字段
  - 首板时间: limitTime 为完整 epoch 秒, 转北京时间 HH:MM
  - 涨停题材: reason 常为空, 回落 concept(板块概念) 再 boardName

用于每日盘后自动生成、App 内查看与导出图片。内置开源中文字体
app/assets/fonts/wqy-microhei.ttc 保证中文渲染。
"""
import datetime
import os
import re
import threading
from collections import Counter

from PIL import Image, ImageDraw, ImageFont

from ..core import config, logger

log = logger.get_logger(__name__)

# ---------- 外观常量(浅色复盘海报风) ----------
WIDTH = 900
MARGIN = 24
PAGE = (242, 244, 248)           # 页面浅灰底
WHITE = (255, 255, 255)
INK = (31, 42, 68)               # 主文字(深藏蓝)
MUTED = (122, 132, 152)          # 次要文字
FAINT = (165, 172, 188)          # 弱文字
RED = (226, 55, 66)              # 涨停红
RED_DEEP = (198, 30, 44)         # 更热题材深红
RED_SOFT = (253, 236, 238)       # 粉底(题材chip/题材文字底)
RED_TEXT = (214, 60, 70)         # 题材红字
GREEN = (26, 158, 116)           # 涨停时间绿
BLUE = (46, 107, 230)            # 主蓝(横幅/标题点缀)
ZEBRA = (247, 249, 252)          # 梯队斑马纹
LINE = (228, 233, 240)           # 分隔线/卡片描边
FB = (240, 130, 32)              # 断板反包橙
FB_SOFT = (255, 243, 230)        # 反包标签粉橙底

# 档位色阶(板数越高越深, 连板标签用)
TIER_COLOR = {
    1: (232, 82, 88),
    2: (226, 62, 72),
    3: (218, 45, 60),
    4: (208, 34, 54),
    5: (196, 24, 50),
    6: (182, 14, 46),
    7: (166, 8, 42),
    "8+": (120, 2, 34),   # ≥8 板(八板及以上)溢出档, 最深
}

FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "fonts", "wqy-microhei.ttc",
)

# 高度板按真实连板数拆分(2026-09-01 主人需求): 开盘啦 pid 只到 5, 真实连板数由东财
# limitUpDays 注入, 按真实板数重分为 1~7 档 + 「八板+」溢出档。
# t >= 8 板(含 8 板)全部归入「八板+」, 8 板不再单列精确档;
# 即档位为 首板~七板 精确 + 八板+ 收敛(2026-09-05 主人确认)。
# (注: 东财缺失的股票回退 pid 档位)
TIERS = [1, 2, 3, 4, 5, 6, 7]
TIER_LABEL = {1: "首板", 2: "二板", 3: "三板", 4: "四板", 5: "五板",
              6: "六板", 7: "七板"}
MAX_TIER = 7
OVER8 = "8+"           # 溢出档 key(真实 ≥8 板, 含 8 板)
OVER8_LABEL = "八板+"  # 8 板及以上统归「八板+」
# 全部渲染档序: 1~7 精确档 + 溢出档(高板在上)
ALL_TIERS = list(TIERS) + [OVER8]

# 卡片布局参数
TIER_COL_W = 76        # 左侧档位标签列宽
FLOW_X0 = TIER_COL_W + 14   # 卡片流起始 x
FLOW_RIGHT = WIDTH - MARGIN
CARD_GAP = 8           # 卡片水平/垂直间距
CARD_PAD = 12          # 卡片内边距
SMALL_H = 64           # 小卡高(≤3板)
BIG_W = 424            # 大卡宽(≥4板)
BIG_H = 96             # 大卡高
BIG_MIN_ZT = 4         # ≥4 板用大卡片

_font_cache = {}


def _font(size):
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    return _font_cache[size]


# ---------- 数据规整 ----------
def _split_concepts(s):
    """「AI安全、AI应用」-> ["AI安全", "AI应用"](顿号/逗号/分号分隔)"""
    return [x.strip() for x in re.split(r"[、,，;；/]", s or "") if x.strip()]


def _collect(data):
    """按真实连板数(zt=max(pid, limitUpDays))重分 1~7 档 + 八板+溢出档;
    返回 (tiers, total, rows): tiers=全档统计(8 档, 含空档),
    rows=[(tier_dict, 股票列表), ...] 只含非空档且高板在前。
    拆分规则(2026-09-05 主人确认): 超过 5 板记录真实几板(6/7 精确),
    真实 ≥8 板(含 8 板 与 9板+)统一归入「八板+」, 不再单列八板档。"""
    grouped = {k: [] for k in ALL_TIERS}
    total = 0
    for pid in (1, 2, 3, 4, 5):
        lst = data.get(pid) or []
        for it in lst:
            try:
                concept = str(it.get("concept", "") or "").strip().replace("\n", " ")
                reason = str(it.get("reason", "") or "").strip()
                # 开盘啦题材常带「(P6)」等概念代码后缀, 展示/统计前清理(2026-09-21)
                reason = re.sub(r"[（(]P?\d{1,3}[)）]\s*$", "", reason).strip()
                board = str(it.get("boardName", "") or it.get("board", "") or "").strip()
                if not reason and concept:
                    reason = concept
                elif not reason:
                    reason = board
                # 概念拆分(2026-09-21): 首个概念做卡片题材展示, 全部概念供热榜聚合统计
                concepts = _split_concepts(reason) or [reason]
                reason = concepts[0]
                # 真实连板数: 优先用当日涨停池注入的真实连板数(解决五板+里 6~8 板区分);
                # 否则回退档位 pid
                zt = max(int(pid), int(it.get("limitUpDays") or pid or 0))
                key = OVER8 if zt > MAX_TIER else zt   # ≥8 板(含8板)归「八板+」
                grouped[key].append({
                    "code": str(it.get("code", "")),
                    "name": str(it.get("name", "")),
                    "zt": zt,
                    "reason": reason,
                    "concepts": concepts,
                    "limitTime": (it.get("limitTime") or 0),
                    "seal": float(it.get("seal", 0) or 0),
                    "turnover": float(it.get("turnover", 0) or 0),
                })
            except (TypeError, ValueError):
                continue
    tiers = []
    rows = []
    for k in ALL_TIERS:
        clean = grouped[k]
        total += len(clean)
        label = OVER8_LABEL if k == OVER8 else TIER_LABEL[k]
        tier = {"label": label, "count": len(clean), "pid": k}
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


def _cn_date(date_str):
    """2026-09-18 -> 9月18日"""
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.month}月{dt.day}日"
    except (TypeError, ValueError):
        return date_str


def _theme_hot(rows, topn=12):
    """题材热榜: 当日全部涨停股 reason 计数 TopN(计数≥2 优先, 不足补齐)。"""
    cnt = Counter()
    for _tier, lst in rows:
        for it in lst:
            # 全部概念拆分聚合(2026-09-21): 「算力、人工智能」两只股各贡献 1 次「算力」
            for t in it.get("concepts") or ([it.get("reason")] if it.get("reason") else []):
                if t and t != "—":
                    cnt[t] += 1
    if not cnt:
        return []
    ranked = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:topn]


def _zt_tag(zt):
    return f"{zt}连板" if zt > 1 else "首板"


# ---------- 卡片流布局 ----------
def _card_content_w(it, big):
    """单个卡片按内容所需宽度(不含内边距)"""
    name_f = _font(20 if big else 16)
    time_f = _font(14 if big else 12)
    name_w = name_f.getbbox(it["name"] or "")[2]
    time_w = time_f.getbbox(_fmt_limit(it["limitTime"]))[2]
    w1 = name_w + 10 + time_w
    if big:
        return w1
    tag_f = _font(11)
    rea_f = _font(13)
    tag = _zt_tag(it["zt"])
    tag_w = tag_f.getbbox(tag)[2] + 14
    rea = _truncate(it["reason"], 150, 13)
    w2 = tag_w + 6 + rea_f.getbbox(rea)[2]
    return max(w1, w2)


def _layout_tier(lst):
    """档位卡片流布局。
    返回 (items, card_w, card_h, n_rows):
      items = [(row, x_off, it), ...] x_off 相对 FLOW_X0。
    ≥4 板(或该档总数 ≤3 只时)用大卡片, 其余小卡片。"""
    big = bool(lst) and (lst[0]["zt"] >= BIG_MIN_ZT or len(lst) <= 3)
    if big:
        card_w, card_h = BIG_W, BIG_H
    else:
        need = max(_card_content_w(it, False) for it in lst) + CARD_PAD * 2
        card_w = int(max(150, min(246, need)))
        card_h = SMALL_H
    per_row = max(1, (FLOW_RIGHT - FLOW_X0 + CARD_GAP) // (card_w + CARD_GAP))
    items = []
    for i, it in enumerate(lst):
        row, col = divmod(i, per_row)
        items.append((row, col * (card_w + CARD_GAP), it))
    n_rows = (len(lst) + per_row - 1) // per_row
    return items, card_w, card_h, n_rows


# ---------- 渲染 ----------
def _chip_color(n):
    """题材热度分档配色: 越热越红(2026-09-21 v2 改良)"""
    if n >= 8:
        return RED_DEEP, WHITE, None            # 深红实底白字
    if n >= 4:
        return RED, WHITE, None                 # 红实底白字
    return RED_SOFT, RED_TEXT, None             # 粉底红字


def build_png(data, date_str, out_path, fanbao=None):
    """渲染连板天梯 PNG(浅色复盘海报风)。fanbao: 断板反包股列表
    [{code,name,reason,change}, ...], 落在首板行的个股加「反包」橙标签。"""
    tiers, total, rows = _collect(data)
    fanbao = fanbao or []
    fb_set = {str(it.get("code", "")) for it in fanbao}
    hot = _theme_hot(rows)

    # ---------- 预布局: 头部高度 ----------
    header_h = 72                       # 日期+标题+风险提示
    chip_font = _font(14)
    chip_rows = []                      # 每行 [(text, w), ...]
    if hot:
        cur, cur_w = [], 0
        for t, n in hot:
            txt = f"{t}({n})"
            w = chip_font.getbbox(txt)[2] + 26
            if cur and cur_w + 8 + w > WIDTH - 2 * MARGIN:
                chip_rows.append(cur)
                cur, cur_w = [], 0
            cur.append((txt, w, n))
            cur_w += w + 8
        if cur:
            chip_rows.append(cur)
    chips_lines = len(chip_rows)
    chips_h = (chips_lines * 30 + 12) if chips_lines else 8

    # ---------- 预布局: 各档高度 ----------
    layouts = []                        # (tier, items, card_w, card_h, n_rows)
    for tier, lst in rows:
        items, cw, ch, nr = _layout_tier(lst)
        layouts.append((tier, items, cw, ch, nr))
    tier_gap = 0                        # 斑马纹行紧贴, 无档间距
    sections_h = sum(nr * ch + (nr - 1) * CARD_GAP + tier_gap
                     for _t, _i, _cw, ch, nr in layouts)
    banner_h = 36                       # 主卡片蓝色横幅
    card_top_pad = 8
    card_bottom_pad = 10
    inner_h = card_top_pad + sections_h + card_bottom_pad
    bottom_pad = 46
    total_h = header_h + chips_h + 6 + banner_h + inner_h + bottom_pad

    img = Image.new("RGB", (WIDTH, total_h), PAGE)
    draw = ImageDraw.Draw(img)

    # ---------- 头部(白底): 日期 + 标题 + 风险提示 ----------
    draw.rectangle([0, 0, WIDTH, header_h + chips_h + 6], fill=WHITE)
    date_txt = _cn_date(date_str)
    draw.text((MARGIN, 12), date_txt, font=_font(30), fill=INK)
    dx = MARGIN + _font(30).getbbox(date_txt)[2] + 12
    draw.text((dx, 10), "连板天梯", font=_font(36), fill=INK)
    # 蓝色装饰短线
    draw.line([MARGIN, 56, MARGIN + 40, 56], fill=BLUE, width=3)
    draw.text((MARGIN + 50, 52),
              "风险提示：内容仅为公开市场数据整理，不构成投资建议，历史表现不代表未来走势",
              font=_font(12), fill=FAINT)

    # ---------- 题材热榜 chips ----------
    cy = header_h + 6
    for row_items in chip_rows:
        cx = MARGIN
        for txt, w, n in row_items:
            fill, fg, _ = _chip_color(n)
            draw.rounded_rectangle([cx, cy, cx + w, cy + 24], radius=12, fill=fill)
            draw.text((cx + 13, cy + 4), txt, font=chip_font, fill=fg)
            cx += w + 8
        cy += 30

    # ---------- 主卡片容器 ----------
    card_x0, card_y0 = MARGIN, header_h + chips_h + 6
    card_x1 = WIDTH - MARGIN
    card_y1 = card_y0 + banner_h + inner_h
    draw.rounded_rectangle([card_x0, card_y0, card_x1, card_y1], radius=12,
                           fill=WHITE, outline=LINE, width=1)
    # 蓝色横幅(仅上半圆角: 先画圆角矩形再用白矩形盖掉下半圆角)
    draw.rounded_rectangle([card_x0, card_y0, card_x1, card_y0 + banner_h + 12],
                           radius=12, fill=BLUE)
    draw.rectangle([card_x0, card_y0 + banner_h, card_x1, card_y0 + banner_h + 12],
                   fill=BLUE)
    draw.text((card_x0 + 16, card_y0 + 8), "连板梯队",
              font=_font(17), fill=WHITE)
    sub = f"共 {total} 家涨停"
    draw.text((card_x1 - 16 - _font(13).getbbox(sub)[2], card_y0 + 11),
              sub, font=_font(13), fill=(214, 228, 252))

    y = card_y0 + banner_h + card_top_pad
    inner_x0 = card_x0 + 8
    inner_x1 = card_x1 - 8

    # ---------- 档位分层(斑马纹) + 卡片流 ----------
    for zi, (tier, items, card_w, card_h, n_rows) in enumerate(layouts):
        zone_h = n_rows * card_h + (n_rows - 1) * CARD_GAP
        # 斑马纹行底
        if zi % 2 == 1:
            draw.rectangle([inner_x0, y, inner_x1, y + zone_h], fill=ZEBRA)
        # 左侧档位标尺(垂直居中): 档名 + n家
        lab_f = _font(20)
        cnt_f = _font(12)
        lab = tier["label"] if tier["pid"] != OVER8 else "八板+"
        cnt_txt = f"{tier['count']}家"
        lab_y = y + (zone_h - 36) // 2
        draw.text((MARGIN + 4, lab_y), lab[:3], font=lab_f, fill=INK)
        draw.text((MARGIN + 6, lab_y + 26), cnt_txt, font=cnt_f, fill=MUTED)
        # 档位竖线
        line_x = FLOW_X0 - 8
        draw.line([line_x, y + 4, line_x, y + zone_h - 4], fill=LINE, width=1)
        # 卡片
        tag_f = _font(11)
        for row, x_off, it in items:
            x0 = FLOW_X0 + x_off
            y0 = y + row * (card_h + CARD_GAP)
            x1, y1 = x0 + card_w, y0 + card_h
            draw.rounded_rectangle([x0, y0, x1, y1], radius=8,
                                   fill=WHITE, outline=LINE, width=1)
            big = card_h >= BIG_H
            t_txt = _fmt_limit(it["limitTime"])
            # 行1: 名称 + 右上涨停时间
            draw.text((x0 + CARD_PAD, y0 + (10 if big else 8)),
                      _truncate(it["name"], card_w - CARD_PAD * 2 - 44, 20 if big else 16),
                      font=_font(20 if big else 16), fill=INK)
            tw = _font(14 if big else 12).getbbox(t_txt)[2]
            draw.text((x1 - CARD_PAD - tw, y0 + (14 if big else 11)),
                      t_txt, font=_font(14 if big else 12), fill=GREEN)
            if big:
                # 行2: 代码 + N连板红标签 + 封单
                code_f = _font(13)
                draw.text((x0 + CARD_PAD, y0 + 42), it["code"],
                          font=code_f, fill=MUTED)
                tag = _zt_tag(it["zt"])
                tgw = tag_f.getbbox(tag)[2] + 14
                tc = TIER_COLOR.get(tier["pid"], TIER_COLOR[OVER8])
                draw.rounded_rectangle([x0 + CARD_PAD + 52, y0 + 38,
                                        x0 + CARD_PAD + 52 + tgw, y0 + 58],
                                       radius=10, fill=tc)
                draw.text((x0 + CARD_PAD + 59, y0 + 41), tag,
                          font=tag_f, fill=WHITE)
                seal_f = _font(13)
                s_txt = f"封单 {_fmt_amount(it['seal'])}"
                sw = seal_f.getbbox(s_txt)[2]
                draw.text((x1 - CARD_PAD - sw, y0 + 42), s_txt,
                          font=seal_f, fill=MUTED)
                # 行3: 题材(+反包)
                rea_x = x0 + CARD_PAD
                if tier["pid"] == 1 and it["code"] in fb_set:
                    rea_x += _draw_fb_tag(draw, rea_x, y0 + 64) + 6
                draw.text((rea_x, y0 + 65),
                          _truncate(it["reason"], x1 - CARD_PAD - rea_x, 13),
                          font=_font(13), fill=RED_TEXT)
            else:
                # 行2: N连板红标签 + 题材(+反包)
                tag = _zt_tag(it["zt"])
                tgw = tag_f.getbbox(tag)[2] + 14
                tc = TIER_COLOR.get(tier["pid"], TIER_COLOR[OVER8])
                tx = x0 + CARD_PAD
                draw.rounded_rectangle([tx, y0 + 34, tx + tgw, y0 + 52],
                                       radius=9, fill=tc)
                draw.text((tx + 7, y0 + 37), tag, font=tag_f, fill=WHITE)
                rea_x = tx + tgw + 8
                if tier["pid"] == 1 and it["code"] in fb_set:
                    rea_x += _draw_fb_tag(draw, rea_x, y0 + 34) + 6
                draw.text((rea_x, y0 + 36),
                          _truncate(it["reason"], x1 - CARD_PAD - rea_x, 13),
                          font=_font(13), fill=RED_TEXT)
        y += zone_h + tier_gap

    # ---------- 底部 ----------
    draw.line([MARGIN, card_y1 + 14, WIDTH - MARGIN, card_y1 + 14],
              fill=LINE, width=1)
    # 2026-10-08 主人定名：「网站和 app 名称都是快选股」⇒ 连板天梯图右下角水印补「股」
    brand = "快选股 Kuaixuan"
    draw.text((WIDTH - MARGIN - _font(13).getbbox(brand)[2], card_y1 + 22),
              brand, font=_font(13), fill=FAINT)

    # ---------- 写出 ----------
    _lock = threading.Lock()
    with _lock:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path, "PNG")
    return out_path


def _draw_fb_tag(draw, x, y):
    """「反包」橙色小标签(粉橙底), 返回宽度"""
    tag = "反包"
    f = _font(11)
    w = f.getbbox(tag)[2] + 12
    draw.rounded_rectangle([x, y, x + w, y + 18], radius=9,
                           fill=FB_SOFT, outline=FB, width=1)
    draw.text((x + 6, y + 2), tag, font=f, fill=FB)
    return w


def generate_for_date(date_str):
    try:
        from . import kpl
        data = kpl.query_ladder_history(date_str)
        total = sum(len(v or []) for v in data.values())
        if total == 0:
            return None
        # 落库数据已注入真实连板数(limitUpDays, 见 kpl._inject_real_limit_days)作为保底;
        # 此处用实时涨停池做最终校准: 仅当实时值 rt>=1 且 >= 落库值时覆盖, 实时缺失则保留
        # 落库值 —— 根治盘后 15:30 东财偶发未就绪导致图回退「五板+」的偶发问题
        real_lb = kpl.real_limit_days(date_str)
        if real_lb:
            for pid, lst in data.items():
                for it in lst:
                    code = str(it.get("code", ""))
                    if code in real_lb:
                        rt = int(real_lb[code] or 0)
                        if rt >= 1:
                            lb = int(it.get("limitUpDays") or pid or 0)
                            it["limitUpDays"] = max(rt, lb)
        # 断板反包检测(近5日内曾涨停 + 昨日断板 + 今日重新涨停)
        fanbao = kpl.fetch_fanbao_stocks(date_str)
        os.makedirs(config.LADDER_IMG_DIR, exist_ok=True)
        out_path = os.path.join(config.LADDER_IMG_DIR, f"{date_str}.png")
        return build_png(data, date_str, out_path, fanbao=fanbao)
    except Exception as e:
        log.error("连板天梯图片生成失败 date=%s err=%s", date_str, e)
        return None
