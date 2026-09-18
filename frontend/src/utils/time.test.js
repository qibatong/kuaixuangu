// time.js 纯函数单测(node:test 零依赖 ESM 版)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pad2, fmtDate, fmtTsDate, todayBj, fmtTsTime, fmtBjDay,
         isPickBlockedTime, isPickGateOn, PICK_BLOCK_MSG_TIME, PICK_BLOCK_MSG_SNAP,
         PICK_BLOCK_FROM, PICK_BLOCK_TO,
         PICK_OPEN } from './time.js'

test('pad2: 补零', () => {
  assert.equal(pad2(5), '05')
  assert.equal(pad2(12), '12')
  assert.equal(pad2(0), '00')
})

test('fmtDate: 日期格式化 YYYY-MM-DD', () => {
  assert.equal(fmtDate(new Date(2026, 7, 15)), '2026-08-15')
  assert.equal(fmtDate(new Date(2026, 0, 5)), '2026-01-05')
})

test('fmtTsDate: 时间戳转日期', () => {
  // 2026-08-15T00:00:00Z = 1784160000? 用精确值: 2026-08-15 00:00 UTC
  const ts = Date.UTC(2026, 7, 15) / 1000
  assert.equal(fmtTsDate(ts), '2026-08-15')
})

test('todayBj: 返回 YYYY-MM-DD 格式', () => {
  assert.match(todayBj(), /^\d{4}-\d{2}-\d{2}$/)
})

test('fmtTsTime: 秒级时间戳(北京时间) → YYYY-MM-DD HH:MM', () => {
  // UTC 01:30 = 北京时间 09:30
  const ts = Date.UTC(2026, 7, 15, 1, 30) / 1000
  assert.equal(fmtTsTime(ts), '2026-08-15 09:30')
  assert.equal(fmtTsTime(0), '-')
  assert.equal(fmtTsTime('2026-08-15'), '2026-08-15')  // 非时间戳原样返回
})

test('fmtBjDay: 秒级时间戳(北京时间) → YYYY-MM-DD', () => {
  const ts = Date.UTC(2026, 7, 15, 1, 30) / 1000
  assert.equal(fmtBjDay(ts), '2026-08-15')
  assert.equal(fmtBjDay(0), '-')
})

// ---- 2026-09-18 选股闸门 v4 (只挡竞价段 9:15:00-9:25:35) ----
// 传参用"北京时间视图的 Date"(与 bjNow() 的返回形态一致), 2026-09-16 = 周三
// 🔴 演进: v4.11.22 挡 9:00-9:26 整段(连盘前一起封, 且前端不看开关) → 9/17 事故回退;
//    v4.11.27 挡 9:00-9:15 + 9:25:00-9:25:35(竞价段放行) ;
//    v4 主人拍板:**只认当日 9:25 定格** → 只挡竞价段, 盘前改为放行+顶栏标注来源日期。
//    秒级粒度是关键(竞价段起止都必须到秒)。
function bj(y, m, d, hh, mm, ss = 0) { return new Date(y, m - 1, d, hh, mm, ss) }

test('选股闸门常量与后端同口径', () => {
  assert.equal(PICK_BLOCK_FROM, 9 * 3600 + 15 * 60)
  assert.equal(PICK_BLOCK_TO, 9 * 3600 + 25 * 60 + 35)
  assert.equal(PICK_OPEN, 9 * 3600 + 25 * 60 + 36)
  // 文案与 backend/app/services/picker/mode.py 逐字一致(后端有对拍单测)
  assert.equal(PICK_BLOCK_MSG_TIME, '竞价进行中 · 9:25 定格后开放')
  assert.equal(PICK_BLOCK_MSG_SNAP, '9:25 竞价定格尚未落库 · 稍后自动恢复')
})

test('isPickBlockedTime: 竞价段 9:15:00-9:25:35 禁用(秒级边界)', () => {
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 14, 59)), false)  // ★ 盘前最后一秒放行
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 15, 0)), true)    // ★ 边界: 含
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 19, 30)), true)   // 竞价进行中
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 24, 59)), true)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 25, 0)), true)    // 当日定格未落库
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 25, 23)), true)   // 实测落库区间
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 25, 32)), true)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 25, 35)), true)   // 边界: 含
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 25, 36)), false)  // 边界: 放行
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 26)), false)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 30)), false)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 14, 0)), false)
})

test('isPickBlockedTime: 盘前 9:00-9:14:59 **放行**(v4.11.22 事故回归防线)', () => {
  // v4.11.22 把盘前(用上交易日定格, 设计内功能)与竞价段混成一段封死;
  // v4 主人拍板"盘前保留但强制标注" ⇒ 必须放行, 由顶栏标注来源日期消歧。
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 0, 30)), false)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 8, 59, 59)), false)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 0, 0)), false)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 5)), false)
  assert.equal(isPickBlockedTime(bj(2026, 9, 16, 9, 14, 59)), false)
})

test('isPickBlockedTime: 周末放行(回放最近交易日定格是既有功能)', () => {
  assert.equal(isPickBlockedTime(bj(2026, 9, 19, 9, 5)), false)       // 周六
  assert.equal(isPickBlockedTime(bj(2026, 9, 20, 9, 25, 10)), false)  // 周日
})

// ---- 2026-09-17 闸门开关联动 ----------------------------------------
// 9/17 早盘事故: 前端置灰只看时间、不看后端开关 → `pick_window_guard=0` 只关了后端,
// 前端依旧置灰且连自动加载都不发请求, 用户完全点不动。
// 以下用例是这条回归的防线(2026-09-17 = 周四)。
test('isPickGateOn: 开关关闭时一律放行(9/17 事故回归防线)', () => {
  assert.equal(isPickGateOn(false, bj(2026, 9, 17, 9, 0)), false)
  assert.equal(isPickGateOn(false, bj(2026, 9, 17, 9, 15)), false)       // 竞价段
  assert.equal(isPickGateOn(false, bj(2026, 9, 17, 9, 25, 10)), false)
  // 0 / null / undefined 等假值同样视为「开关关闭」
  assert.equal(isPickGateOn(0, bj(2026, 9, 17, 9, 15)), false)
  assert.equal(isPickGateOn(null, bj(2026, 9, 17, 9, 15)), false)
  assert.equal(isPickGateOn(undefined, bj(2026, 9, 17, 9, 15)), false)
})

test('isPickGateOn: 开关开启时与纯时间口径逐点一致', () => {
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 8, 59)), false)
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 9, 0)), false)      // 新口径: 盘前放行
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 9, 15)), true)      // 新口径: 竞价段拦
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 9, 20)), true)
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 9, 25, 10)), true)
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 9, 25, 36)), false)
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 9, 26)), false)
  assert.equal(isPickGateOn(true, bj(2026, 9, 17, 10, 20)), false)
  assert.equal(isPickGateOn(true, bj(2026, 9, 19, 9, 15)), false)   // 周六
})

test('isPickGateOn: 与 isPickBlockedTime 的等价关系(开关为真时)', () => {
  const samples = [[8, 59], [9, 0], [9, 14, 59], [9, 15], [9, 25, 0], [9, 25, 35],
                   [9, 25, 36], [9, 26], [12, 0]]
  for (const [hh, mm, ss = 0] of samples) {
    const t = bj(2026, 9, 17, hh, mm, ss)
    assert.equal(isPickGateOn(true, t), isPickBlockedTime(t))
  }
})
