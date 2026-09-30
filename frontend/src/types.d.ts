// 2026-10-01 v4.11.84 (P2-4): 全站共享的**核心数据类型**（渐进式类型检查的第一批）
//
// 用法：JS 里 JSDoc 直接引用即可（本文件**不写 import/export**，故是全局声明，无需 import）：
//   /** @type {PickItem[]} */  /  /** @param {LockedStockList} locked */
//
// 🔴 这些类型只用于 `npm run typecheck`（tsc --noEmit）校验，**不参与构建** —— vite 仍按原样打包 JS，
//   运行时零开销、零风险。范围先锁在"数据主干"（request/stocks/format/filters/strategy），
//   后续要扩到更多文件时，往 tsconfig.json 的 include 里加即可。

/** 选股名单里的一行（竞价 / 实时两套策略共用；spot 侧无 entityChange） */
interface PickItem {
  code: string
  name?: string
  /** 评分 0~100（五因子加权） */
  probability?: number | null
  /** 可信度 55~90 */
  confidence?: number | null
  /** 竞价涨幅 % */
  bidChange?: number | null
  /** 实时涨幅 % */
  realChange?: number | null
  /** 实体涨幅 %（仅竞价：现价 ÷ 今开 − 1） */
  entityChange?: number | null
  /** 竞价金额（万） */
  bidAmt?: number | null
  bidRatio?: number | null
  /** 实际流通市值（亿） */
  circulationMV?: number | null
  warnType?: string | number | null
  industry?: string
  concept?: string
  rank?: number
  /** 后端偶有新增字段，留宽松索引（避免每次加字段都要改类型） */
  [k: string]: any
}

/**
 * `stores/stocks.js → loadLockedBatchFromServer()` 的返回。
 *
 * 🔴 **这不是普通数组**：`autoApplied` / `freezeDate` / `freezeIsToday` 是**挂在数组对象上的自定义属性**
 *   （`stocks.autoApplied = isAuto` 那几行），不是数组元素。
 *   因此任何 `[...locked]`、`locked.filter()`、`JSON.parse(JSON.stringify(...))` 的结果都会**丢掉**它们 ——
 *   `mergeSpotIntoLocked()` 里的 `locked.autoApplied` 就是靠这个约定工作的：
 *   丢掉 ⇒ 退化成 `undefined` ⇒ "自动批次不过滤"的规则失效 ⇒ 名单会被个人筛选条件过滤掉（**静默**行为变化）。
 *   改这附近代码时请一并检查调用点；理想的下一步是改成 `{ stocks, autoApplied, ... }` 结构（本次不动）。
 */
type LockedStockList = PickItem[] & {
  autoApplied?: boolean
  freezeDate?: string
  freezeIsToday?: boolean
}
