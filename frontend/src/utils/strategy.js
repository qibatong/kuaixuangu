/* ==========================================================================
   tab(leftTab) → 引擎(store.strategy) 的**唯一映射**（2026-09-30 收敛）

   ⚠️ 两组取值同名但**不同义**，这是本项目最容易看错的一处：
     · `leftTab`  的 'auction'/'spot' = **用哪套 UI**（tab1 锁定那套 / tab2 实时那套）
     · `strategy` 的 'auction'/'spot' = **用哪个引擎**（定格竞价 / 盘中实时）

   🔴 沿革与本次修复
     2026-09-28 v4.11.80 第三步：为满足「AI竞价出来的数据就锁定」，让 tab1「AI选股」
     也改走 **spot 引擎**，映射写成**白名单式**：

         m === 'aipick' || m === 'aipick_lgb' || m === 'yijiner' || m === 'zhpick'
           ? 'auction' : 'spot'

     该写法**漏掉了 `'auction'` 自己** ⇒ 点「竞价选股」(`m='auction'`) 落进"其余"桶
     ⇒ `strategy='spot'` ⇒ ① FilterPanel 走 spot 分支，**隐藏「竞涨」「竞额」**；
     ② 表格读 spotStocks；③ 取数走 `/api/stocks?strategy=spot`（生产实测仅 1~3 只）。
     而 tab2 的值 `'spot'` 恰好与兜底值同名 ⇒ **歪打正着**，只有 tab1 受伤。

     2026-09-30 主人拍板回退（方案 A）：**只有 `'spot'` 走 spot 引擎**，其余一律 auction。
     - 需求「出来数据就锁定」属**锁定动作**，回退不影响它：tab1 的全套锁定语义
       （定格标注条 / 9:26 闸门 / 配额门禁 / 落批次 / 进自选池）一字未动，只换回引擎。
     - 回退动因：tab 已改名「竞价选股」而引擎是 spot ⇒ 名实不符；且因此把竞价口径下
       **真正生效**的门槛（竞额下限 bidAmtFloor）藏进了 spot 面板 —— 主人"看不到条件
       却几乎不出数据"（1 只）的根因。
     - tab2「实时动态选股」不受影响（本就不需要 auction）。
   ========================================================================== */

export const TAB_AUCTION = 'auction'
export const TAB_SPOT = 'spot'

/**
 * tab → 引擎。**唯一入口**，别在别处再写一份映射（写两处必漂移）。
 *
 * - `'spot'`（实时动态选股）⇒ `'spot'`
 * - 其余一律 ⇒ `'auction'`：含 `'auction'`（竞价选股，本函数存在的首要目的）、
 *   aipick 组（`aipick` / `aipick_lgb` / `yijiner` / `zhpick` —— 名单来自各自端点、
 *   **不读** `store.strategy`，归 auction 只为不给 FilterPanel 留脏值），以及任何未知值。
 *
 * 兜底取 `'auction'`：它是 `store.strategy` 的初值、也是"中性"值 —— 未知 tab 归 spot
 * 会让尚未渲染/已隐藏的 FilterPanel 拿到一个"以为在盘中模式"的脏值（9/28 注释原话）。
 *
 * @param {string} leftTab 当前 tab 标识
 * @returns {'auction'|'spot'} 引擎标识
 */
export function strategyForTab(leftTab) {
  return leftTab === TAB_SPOT ? TAB_SPOT : TAB_AUCTION
}
