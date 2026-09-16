import { Box, Building2, Database, Ruler, Shapes } from 'lucide-react'

// 组件用于展示后端解析出的模型统计、构件分类、楼层和工程量摘要。

// 将文件大小格式化后显示在模型概览卡片中。
function formatBytes(value) {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`
}

export default function ModelOverview({ model }) {
  if (!model) {
    return (
      <div className="overview-empty">
        <Box size={24} />
        <span>选择模型后显示解析结果</span>
      </div>
    )
  }

  const statistics = model.statistics || {}
  const categories = model.category_counts || []
  // 以数量最多的分类作为进度条基准，保证所有分类宽度处于 0-100%。
  const maxCount = Math.max(...categories.map((item) => item.count), 1)
  // 概览只展示前六项工程量，完整数据仍保留在模型详情中。
  const quantityEntries = Object.entries(model.quantity_totals || {}).slice(0, 6)

  return (
    <div className="model-overview">
      {/* 顶部四项关键统计。 */}
      <div className="metric-grid">
        <article>
          <Shapes size={17} />
          <span>构件</span>
          <strong>{Number(statistics.element_count || 0).toLocaleString()}</strong>
        </article>
        <article
          title={
            model.occupied_floors == null
              ? ''
              : model.storeys?.length > 0
                ? `可居住楼层；IFC 楼层实体共 ${model.storeys.length} 个（含基础/吊顶/屋面）`
                : '楼层数来自配套 PDF 图纸标高标注（该模型 IFC 未声明楼层实体）'
          }
        >
          <Building2 size={17} />
          <span>楼层</span>
          <strong>{model.occupied_floors ?? '—'}</strong>
        </article>
        <article>
          <Database size={17} />
          <span>IFC 实体</span>
          <strong>{Number(statistics.entity_count || 0).toLocaleString()}</strong>
        </article>
        <article>
          <Ruler size={17} />
          <span>模型大小</span>
          <strong>{formatBytes(model.file_size)}</strong>
        </article>
      </div>

      <div className="overview-columns">
        {/* 构件分类分布，每个分类使用相对柱长表示占比。 */}
        <section>
          <h3>构件分布</h3>
          <div className="distribution-list">
            {categories.slice(0, 6).map((item) => (
              <div key={item.name}>
                <span>{item.name}</span>
                <div>
                  <i style={{ width: `${Math.max((item.count / maxCount) * 100, 3)}%` }} />
                </div>
                <strong>{item.count.toLocaleString()}</strong>
              </div>
            ))}
          </div>
        </section>
        {/* 楼层构件数量和标高的紧凑摘要。 */}
        <section>
          <h3>楼层与数量指标</h3>
          {model.storeys?.length > 0 ? (
            <div className="storey-list">
              {model.storeys.slice(0, 4).map((storey) => (
                <div key={storey.id}>
                  <span>{storey.name}</span>
                  <span>{storey.element_count} 构件</span>
                  <span>
                    {storey.elevation_m === null
                      ? '标高未定义'
                      : `${storey.elevation_m.toFixed(2)} m`}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="storey-empty">
              该模型未声明楼层实体（IfcBuildingStorey），标高类问题无法回答。
            </div>
          )}
          {quantityEntries.length > 0 && (
            <div className="quantity-list">
              {quantityEntries.map(([name, value]) => (
                <span key={name}>
                  {name} <strong>{Number(value).toLocaleString()}</strong>
                </span>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
