import { ref, computed } from 'vue'

/**
 * 通用表格排序 composable（复用 StockTable 的排序逻辑）。
 *
 * 用法：
 *   const sort = useSortable()
 *   // 表头: <th class="sortable" :class="{ active: sort.keyOf('code') }" @click="sort.onSort('code', 'string')">代码<span class="sort-ind">{{ sort.ind('code') }}</span></th>
 *   // 数据: v-for="it in sort.sorted(list)"
 *   // 列值函数可选: sort.sorted(list, it => it.bid_amt ?? 0)  或  sort.sorted(list, (it, key) => 自定义(it, key))
 *
 * 规则：
 *   - 点击切换: 无 → 降序(数值列) / 升序(字符串列) → 反向 → 无
 *   - null/undefined/''/NaN 始终排到末尾(无论升降)
 *   - 字符串列按中文 localeCompare 比较
 */
export function useSortable() {
  // { key, dir: 'asc' | 'desc', type: 'string' | 'number' } 或 null
  const sortState = ref(null)

  function onSort(key, type = 'number') {
    if (!sortState.value || sortState.value.key !== key) {
      sortState.value = { key, dir: type === 'string' ? 'asc' : 'desc', type }
    } else if (sortState.value.dir === 'desc') {
      sortState.value = { key, dir: 'asc', type }
    } else {
      sortState.value = null
    }
  }

  function ind(key) {
    if (!sortState.value || sortState.value.key !== key) return ''
    return sortState.value.dir === 'desc' ? ' ↓' : ' ↑'
  }

  function keyOf(key) {
    return sortState.value ? sortState.value.key === key : false
  }

  function clear() {
    sortState.value = null
  }

  const isActive = computed(() => !!sortState.value)
  const dir = computed(() => (sortState.value ? sortState.value.dir : null))

  // 排序后的列表（不修改原数组）
  // getVal 支持两种签名: (it) => value 或 (it, key) => value(自定义多列取值, 如封单表按时点取数)
  function sorted(list, getVal) {
    if (!sortState.value) return list
    const { key, dir, type } = sortState.value
    const mult = dir === 'asc' ? 1 : -1
    const valOf = getVal || ((it) => it[key])
    return [...list].sort((a, b) => {
      const av = valOf(a, key)
      const bv = valOf(b, key)
      const aNull = av === null || av === undefined || av === '' || (typeof av === 'number' && isNaN(av))
      const bNull = bv === null || bv === undefined || bv === '' || (typeof bv === 'number' && isNaN(bv))
      if (aNull && bNull) return 0
      if (aNull) return 1
      if (bNull) return -1
      if (type === 'string') return mult * String(av).localeCompare(String(bv), 'zh-Hans-CN')
      return mult * (Number(av) - Number(bv))
    })
  }

  return { sortState, onSort, ind, keyOf, clear, isActive, dir, sorted }
}
