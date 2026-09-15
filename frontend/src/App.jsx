import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  Blocks,
  BookOpen,
  CheckCircle2,
  FileBox,
  Layers3,
  LoaderCircle,
  RefreshCw,
  UploadCloud,
} from 'lucide-react'
import BimViewer from './components/BimViewer'
import ChatPanel from './components/ChatPanel'
import ElementTable from './components/ElementTable'
import ModelOverview from './components/ModelOverview'
import {
  getHealth,
  getKnowledgeStatus,
  getModel,
  getModels,
  uploadModel,
} from './api'
import './App.css'

// 将字节数转换为便于界面展示的 B/KB/MB/GB 单位。
function formatBytes(value) {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`
}

// 只把问答需要的模型摘要传给聊天组件，避免携带完整构件列表。
function modelContextForChat(model) {
  if (!model) return null
  return {
    id: model.id,
    file_name: model.file_name,
    file_size: model.file_size,
    format: model.format,
    schema: model.schema,
    project: model.project,
    statistics: model.statistics,
    storeys: model.storeys,
    element_counts: model.element_counts,
    category_counts: model.category_counts,
    units: model.units,
    quantity_totals: model.quantity_totals,
    top_properties: model.top_properties?.slice(0, 30),
  }
}

function App() {
  // 模型库、当前选中模型和详情解析结果。
  const [models, setModels] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [modelDetail, setModelDetail] = useState(null)

  // 后端健康状态、知识库状态与各类加载/错误状态。
  const [health, setHealth] = useState(null)
  const [knowledge, setKnowledge] = useState(null)
  const [loadingModel, setLoadingModel] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  // 主面板当前标签页，以及 BimViewer 上报的渲染统计数据。
  const [activeTab, setActiveTab] = useState('overview')
  const [viewerStats, setViewerStats] = useState(null)

  const selectedModel = models.find((model) => model.id === selectedId) || null
  const selectedModelIsParseable = Boolean(selectedModel?.parseable)

  // 重新从后端同步模型列表，并尽量保留当前选中的模型。
  const refreshModels = useCallback(async (preferredId) => {
    const payload = await getModels()
    setModels(payload.items)
    setSelectedId((current) => preferredId || current || payload.items[0]?.id || '')
    return payload.items
  }, [])

  // 首次进入页面时并行获取模型列表、服务状态和知识库状态。
  useEffect(() => {
    let cancelled = false
    Promise.all([refreshModels(), getHealth(), getKnowledgeStatus()])
      .then(([modelItems, healthPayload, knowledgePayload]) => {
        if (cancelled) return
        setHealth(healthPayload)
        setKnowledge(knowledgePayload)
        const firstIfc = modelItems.find((model) => model.parseable)
        if (firstIfc) setSelectedId(firstIfc.id)
      })
      .catch((requestError) => {
        if (!cancelled) setError(requestError.message)
      })
    return () => {
      cancelled = true
    }
  }, [refreshModels])

  // 选中模型后按需加载完整解析详情；取消标记用于避免旧请求覆盖新选择。
  useEffect(() => {
    if (!selectedId || !selectedModelIsParseable) {
      setModelDetail(null)
      setLoadingModel(false)
      return undefined
    }
    let cancelled = false
    setLoadingModel(true)
    setError('')
    setViewerStats(null)
    setModelDetail(null)
    getModel(selectedId)
      .then((payload) => {
        if (!cancelled) setModelDetail(payload)
      })
      .catch((requestError) => {
        if (!cancelled) setError(requestError.message)
      })
      .finally(() => {
        if (!cancelled) setLoadingModel(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId, selectedModelIsParseable])

  // 上传成功后刷新列表并自动选中新文件，解析失败时保留文件但不阻塞界面。
  const submitModelFile = async (file) => {
    if (!file) return
    setUploading(true)
    setError('')
    try {
      const uploaded = await uploadModel(file)
      await refreshModels(uploaded.id)
      setSelectedId(uploaded.id)
      setActiveTab('overview')
      if (uploaded.parse_error) setError(uploaded.parse_error)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setUploading(false)
    }
  }

  // 文件选择框和拖拽区域最终都复用同一套上传逻辑。
  const handleUpload = (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    submitModelFile(file)
  }

  const handleModelDrop = (event) => {
    event.preventDefault()
    if (uploading) return
    submitModelFile(event.dataTransfer.files?.[0])
  }

  const handleViewerStats = useCallback((stats) => {
    setViewerStats(stats)
  }, [])

  const contentUrl =
    selectedModel?.parseable && modelDetail
      ? `/api/models/${encodeURIComponent(selectedModel.id)}/content`
      : null

  return (
    <div className="app-shell">
      {/* 顶部栏仅展示品牌和 AI 连接状态，文件提交统一放在左侧模型库。 */}
      <header className="app-header">
        <div className="brand">
          <span className="brand__mark">
            <Blocks size={22} />
          </span>
          <div>
            <strong>BIM Intelligence</strong>
            <span>IFC 解析与模型问答</span>
          </div>
        </div>
        <div className="app-header__actions">
          <div className={`connection ${health?.ai?.configured ? 'is-online' : ''}`}>
            {health?.ai?.configured ? (
              <CheckCircle2 size={15} />
            ) : (
              <AlertTriangle size={15} />
            )}
            <span>
              {health?.ai?.configured
                ? `${health.ai.model} 已连接`
                : 'DeepSeek 待配置'}
            </span>
          </div>
        </div>
      </header>

      {error && (
        <div className="app-error">
          <AlertTriangle size={16} />
          <span>{error}</span>
          <button type="button" onClick={() => setError('')}>
            关闭
          </button>
        </div>
      )}

      <div className="workspace">
        {/* 左侧模型库：上传入口、模型切换和知识库状态。 */}
        <aside className="library-panel">
          <div className="panel-heading">
            <div>
              <Layers3 size={18} />
              <div>
                <h2>模型库</h2>
                <span>{models.length} 个文件</span>
              </div>
            </div>
            <button
              type="button"
              onClick={() => refreshModels()}
              title="刷新模型列表"
              aria-label="刷新模型列表"
            >
              <RefreshCw size={16} />
            </button>
          </div>

          <label
            className={`model-submit ${uploading ? 'is-loading' : ''}`}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleModelDrop}
          >
            {uploading ? (
              <LoaderCircle className="spin" size={20} />
            ) : (
              <UploadCloud size={20} />
            )}
            <strong>{uploading ? '正在提交并解析' : '提交 BIM 模型'}</strong>
            <span>拖放或选择 IFC / RVT 文件</span>
            <input
              type="file"
              accept=".ifc,.rvt"
              onChange={handleUpload}
              disabled={uploading}
            />
          </label>

          <div className="model-list">
            {models.map((model) => (
              <button
                type="button"
                className={model.id === selectedId ? 'is-active' : ''}
                key={model.id}
                onClick={() => {
                  setSelectedId(model.id)
                  setActiveTab('overview')
                }}
              >
                <span className={`file-type file-type--${model.format.toLowerCase()}`}>
                  {model.format}
                </span>
                <span className="model-list__text">
                  <strong>{model.file_name}</strong>
                  <small>
                    {formatBytes(model.file_size)}
                    {model.parseable ? ' · 可解析' : ' · 需导出 IFC'}
                  </small>
                </span>
              </button>
            ))}
            {!models.length && (
              <div className="library-empty">
                <FileBox size={23} />
                <span>暂无模型</span>
              </div>
            )}
          </div>

          <div className="library-footer">
            <div>
              <BookOpen size={16} />
              <span>知识资料</span>
            </div>
            <strong>
              {knowledge?.documents ?? 0} 文档 · {knowledge?.chunks ?? 0} 片段
            </strong>
            <small>
              {knowledge?.state === 'ready'
                ? '本地索引可用'
                : knowledge?.state === 'indexing'
                  ? '正在建立索引'
                  : '将在首次问答时建立'}
            </small>
          </div>
        </aside>

        {/* 中间主区域：IFC 三维视图与解析结果面板。 */}
        <main className="model-panel">
          <header className="panel-heading model-panel__heading">
            <div>
              <FileBox size={18} />
              <div>
                <h2>
                  {modelDetail?.project?.long_name ||
                    modelDetail?.project?.name ||
                    selectedModel?.file_name ||
                    'IFC 模型'}
                </h2>
                <span>
                  {modelDetail
                    ? [
                        modelDetail.schema,
                        modelDetail.project?.name !==
                        (modelDetail.project?.long_name ||
                          modelDetail.project?.name)
                          ? modelDetail.project?.name
                          : null,
                        modelDetail.file_name,
                      ]
                        .filter(Boolean)
                        .join(' · ')
                    : selectedModel
                      ? `${selectedModel.format} 文件`
                      : '请选择模型'}
                </span>
              </div>
            </div>
            {modelDetail && (
              <div className="model-heading-stats">
                <span>{modelDetail.statistics.element_count.toLocaleString()} 构件</span>
                <span>{modelDetail.statistics.storey_count} 楼层</span>
              </div>
            )}
          </header>

          <div className="viewer-stage">
            {/* RVT 不能直接渲染，只有 IFC 会创建 WebGL 查看器。 */}
            {selectedModel?.parseable ? (
              <BimViewer
                contentUrl={contentUrl}
                file={null}
                modelName={modelDetail?.file_name || selectedModel.file_name}
                onStats={handleViewerStats}
              />
            ) : (
              <div className="format-notice">
                <AlertTriangle size={28} />
                <h3>RVT 无法在浏览器中直接解析</h3>
                <p>请先在 Revit 中导出为 IFC，再上传该 IFC 文件。</p>
              </div>
            )}
            {loadingModel && (
              <div className="viewer-loading">
                <LoaderCircle className="spin" size={22} />
                <span>正在解析 IFC 数据</span>
              </div>
            )}
          </div>

          <div className="analysis-panel">
            <div className="tab-bar">
              <button
                type="button"
                className={activeTab === 'overview' ? 'is-active' : ''}
                onClick={() => setActiveTab('overview')}
              >
                模型概览
              </button>
              <button
                type="button"
                className={activeTab === 'elements' ? 'is-active' : ''}
                onClick={() => setActiveTab('elements')}
                disabled={!modelDetail}
              >
                构件清单
              </button>
              {viewerStats && (
                <span className="tab-bar__meta">
                  渲染 {viewerStats.geometries.toLocaleString()} 几何体
                </span>
              )}
            </div>
            <div className="analysis-panel__content">
              {activeTab === 'overview' ? (
                <ModelOverview model={modelDetail} />
              ) : (
                <ElementTable model={modelDetail} />
              )}
            </div>
          </div>
        </main>

        {/* 右侧聊天面板使用当前模型的摘要和构件匹配结果作为问答上下文。 */}
        <ChatPanel
          modelId={selectedModel?.parseable ? selectedId : null}
          modelName={modelDetail?.file_name || selectedModel?.file_name}
          modelContext={modelContextForChat(modelDetail)}
          aiConfigured={Boolean(health?.ai?.configured)}
        />
      </div>
    </div>
  )
}

export default App
