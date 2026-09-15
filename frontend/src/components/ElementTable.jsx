import { useEffect, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Search,
  SlidersHorizontal,
} from 'lucide-react'
import { getElements } from '../api'

// 构件表按页从后端读取数据，避免一次性把大型 IFC 的全部构件加载到浏览器。
const PAGE_SIZE = 50

export default function ElementTable({ model }) {
  // 分页、搜索、类型过滤以及异步请求状态。
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [query, setQuery] = useState('')
  const [type, setType] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 切换模型时清空筛选条件和页码，避免沿用上一个模型的查询范围。
  useEffect(() => {
    setPage(0)
    setQuery('')
    setType('')
  }, [model?.id])

  // 查询参数变化后重新请求数据；搜索输入使用短延迟减少连续按键产生的请求。
  useEffect(() => {
    if (!model?.id) return undefined
    let cancelled = false
    const timer = window.setTimeout(async () => {
      setLoading(true)
      setError('')
      try {
        const payload = await getElements(model.id, {
          q: query,
          element_type: type,
          offset: page * PAGE_SIZE,
          limit: PAGE_SIZE,
        })
        if (!cancelled) {
          setItems(payload.items)
          setTotal(payload.total)
        }
      } catch (requestError) {
        if (!cancelled) setError(requestError.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, query ? 220 : 0)

    // 请求未完成时组件被卸载，或查询条件再次变化，旧响应将被丢弃。
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [model?.id, page, query, type])

  // 总数为 0 时仍保留第 0 页的语义，避免分页器显示负数。
  const maxPage = Math.max(Math.ceil(total / PAGE_SIZE) - 1, 0)

  return (
    <div className="element-table">
      {/* 工具栏负责名称/GlobalId 搜索、IFC 类型过滤和结果计数。 */}
      <div className="element-table__toolbar">
        <label className="search-field">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setPage(0)
            }}
            placeholder="搜索名称、类型或 GlobalId"
          />
        </label>
        <label className="filter-field">
          <SlidersHorizontal size={15} />
          <select
            value={type}
            onChange={(event) => {
              setType(event.target.value)
              setPage(0)
            }}
          >
            <option value="">全部构件类型</option>
            {model?.element_counts?.map((item) => (
              <option value={item.type} key={item.type}>
                {item.label} · {item.count}
              </option>
            ))}
          </select>
        </label>
        <span className="element-table__count">{total.toLocaleString()} 项</span>
      </div>

      {error && <div className="inline-error">{error}</div>}
      {/* 表格仅展示当前页构件及其数量属性摘要。 */}
      <div className="element-table__scroll">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>IFC 类型</th>
              <th>楼层</th>
              <th>材质</th>
              <th>数量指标</th>
            </tr>
          </thead>
          <tbody className={loading ? 'is-loading' : ''}>
            {items.map((item) => {
              const quantities = Object.entries(item.quantities || {}).slice(0, 2)
              return (
                <tr key={item.id}>
                  <td>
                    <strong>{item.name || item.label}</strong>
                    <span>{item.global_id || `#${item.id}`}</span>
                  </td>
                  <td>
                    <span className="type-tag">{item.label}</span>
                  </td>
                  <td>{item.storey || '未分配'}</td>
                  <td>{item.materials?.join('、') || '未关联'}</td>
                  <td>
                    {quantities.length
                      ? quantities
                          .map(([name, value]) => `${name} ${value}`)
                          .join(' · ')
                      : `${Object.keys(item.properties || {}).length} 项属性`}
                  </td>
                </tr>
              )
            })}
            {!items.length && !loading && (
              <tr>
                <td colSpan="5" className="table-empty">
                  没有匹配的构件
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 上一页和下一页始终受当前页码及加载状态约束。 */}
      <div className="element-table__pagination">
        <span>
          第 {total ? page + 1 : 0} / {maxPage + 1} 页
        </span>
        <div>
          <button
            type="button"
            onClick={() => setPage((value) => Math.max(value - 1, 0))}
            disabled={page === 0 || loading}
            title="上一页"
            aria-label="上一页"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            type="button"
            onClick={() => setPage((value) => Math.min(value + 1, maxPage))}
            disabled={page >= maxPage || loading}
            title="下一页"
            aria-label="下一页"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
