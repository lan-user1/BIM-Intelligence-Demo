import { useEffect, useRef, useState } from 'react'
import { Bot, CornerDownLeft, MessageSquareText, StopCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import { streamChat } from '../api'

// 空会话状态下的示例问题：前两条走规则引擎秒答，第三条演示 LLM 兜底。
const suggestions = [
  '这栋楼一共有几扇门？',
  '哪一层的墙最多？',
  '介绍一下这栋楼的结构',
]

export default function ChatPanel({
  modelId,
  modelName,
  modelContext,
  aiConfigured,
}) {
  // 消息列表、输入框、流式加载状态，以及滚动和取消请求引用。
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const abortRef = useRef(null)
  const listRef = useRef(null)

  // 切换模型时开启新会话，避免把上一个模型的回答作为当前模型的历史。
  useEffect(() => {
    setMessages([])
    setError('')
  }, [modelId])

  // 消息或加载状态变化后自动滚动到底部，便于观察流式回答。
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages, loading])

  // 发送问题时构造历史上下文，并为助手回答预留一条可增量更新的消息。
  const send = async (value = input) => {
    const question = value.trim()
    if (!question || loading) return
    if (!aiConfigured) {
      setError('后端尚未配置 DEEPSEEK_API_KEY。')
      return
    }

    setInput('')
    setError('')
    setLoading(true)
    const history = messages
      .filter((message) => !message.error)
      .map(({ role, content }) => ({ role, content }))
    const userMessage = { role: 'user', content: question }
    // history 不包含占位回答，因此新助手消息的位置正好是 history.length + 1。
    const assistantIndex = history.length + 1
    setMessages((current) => [
      ...current,
      userMessage,
      { role: 'assistant', content: '', sources: [] },
    ])

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat(
        {
          question,
          history,
          model_id: modelId || null,
          model_context: modelContext || null,
        },
        {
          // meta 事件先返回引用来源和实际使用的模型名称。
          onMeta(meta) {
            setMessages((current) => {
              const next = [...current]
              if (next[assistantIndex]) {
                next[assistantIndex] = {
                  ...next[assistantIndex],
                  mode: meta.mode || 'llm',
                  sources: meta.sources || [],
                  provider: meta.model,
                  evidence: meta.evidence || [],
                  note: meta.note || '',
                }
              }
              return next
            })
          },
          // token 事件持续追加文本，保留已有内容以形成流式回答。
          onToken(token) {
            setMessages((current) => {
              const next = [...current]
              const target = next[assistantIndex]
              if (target) {
                next[assistantIndex] = {
                  ...target,
                  content: `${target.content}${token}`,
                }
              }
              return next
            })
          },
        },
        controller.signal,
      )
    } catch (requestError) {
      if (requestError.name === 'AbortError') {
        // 用户主动停止时保留已经生成的内容，并给出明确的结束提示。
        setMessages((current) => {
          const next = [...current]
          const target = next[assistantIndex]
          if (target && !target.content) {
            next[assistantIndex] = {
              ...target,
              content: '已停止生成。',
            }
          }
          return next
        })
      } else {
        // 请求或模型错误会显示在面板中，并移除尚未产生内容的空占位消息。
        setError(requestError.message)
        setMessages((current) => {
          const next = [...current]
          const target = next[assistantIndex]
          if (target && !target.content) next.splice(assistantIndex, 1)
          return next
        })
      }
    } finally {
      // 无论成功、失败或取消，都恢复输入状态并释放取消控制器。
      setLoading(false)
      abortRef.current = null
    }
  }

  return (
    <section className="chat-panel">
      {/* 标题区展示当前模型和 AI 配置状态。 */}
      <header className="panel-heading chat-panel__heading">
        <div>
          <Bot size={18} />
          <div>
            <h2>模型助手</h2>
            <span>
              {[
                modelName || '未选择模型',
                modelContext?.statistics
                  ? `${modelContext.statistics.element_count} 构件`
                  : null,
                modelContext?.statistics
                  ? `${modelContext.statistics.storey_count} 楼层`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </div>
        </div>
        <span className={`status-pill ${aiConfigured ? 'is-online' : ''}`}>
          {aiConfigured ? 'DeepSeek' : '待配置'}
        </span>
      </header>

      {/* 消息区域使用 Markdown 渲染回答，并在回答下方显示引用来源。 */}
      <div ref={listRef} className="chat-panel__messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <MessageSquareText size={24} />
            <h3>询问当前 IFC 模型</h3>
            <p>回答会结合模型统计、构件属性与本地 BIM 资料。</p>
          </div>
        )}
        {messages.map((message, index) => (
          <article
            className={`chat-message chat-message--${message.role}`}
            key={`${message.role}-${index}`}
          >
            <div className="chat-message__meta">
              <span>{message.role === 'user' ? '你' : 'BIM AI'}</span>
              {message.role === 'assistant' && message.mode === 'rule' && (
                <span className="mode-badge mode-badge--rule">规则引擎</span>
              )}
              {message.role === 'assistant' && message.mode === 'llm' && message.provider && (
                <span className="mode-badge mode-badge--llm">{message.provider}</span>
              )}
            </div>
            <div className="chat-message__content">
              {message.content ? (
                <div className="chat-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                    {message.content}
                  </ReactMarkdown>
                </div>
              ) : loading ? (
                '正在分析模型上下文...'
              ) : (
                ''
              )}
            </div>
            {message.sources?.length > 0 && (
              <div className="chat-message__sources">
                {message.sources.slice(0, 4).map((source) => (
                  <span key={`${source.type}-${source.title}`}>
                    {source.title}
                    {source.location ? ` · ${source.location}` : ''}
                  </span>
                ))}
              </div>
            )}
            {message.evidence?.length > 0 && (
              <div className="chat-message__evidence">
                <span className="chat-message__evidence-label">
                  证据 · {message.evidence.length} 项
                </span>
                {message.evidence.slice(0, 6).map((gid) => (
                  <code key={gid} className="chat-message__evidence-id" title={gid}>
                    {gid}
                  </code>
                ))}
                {message.evidence.length > 6 && (
                  <span className="chat-message__evidence-more">
                    +{message.evidence.length - 6}
                  </span>
                )}
              </div>
            )}
            {message.note && <div className="chat-message__note">{message.note}</div>}
          </article>
        ))}
      </div>

      {messages.length === 0 && (
        /* 没有历史消息时展示快捷问题。 */
        <div className="chat-suggestions">
          {suggestions.map((suggestion) => (
            <button
              type="button"
              key={suggestion}
              onClick={() => send(suggestion)}
              disabled={loading || !modelId}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {error && <div className="inline-error">{error}</div>}

      {/* 输入框支持 Enter 发送、Shift+Enter 换行；生成期间按钮变为停止操作。 */}
      <div className="chat-composer">
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              send()
            }
          }}
          placeholder={modelId ? '输入模型相关问题' : '请先选择 IFC 模型'}
          rows={3}
          disabled={!modelId || loading}
        />
        <button
          type="button"
          className={loading ? 'composer-stop' : 'composer-send'}
          onClick={loading ? () => abortRef.current?.abort() : () => send()}
          disabled={!loading && (!input.trim() || !modelId)}
          title={loading ? '停止生成' : '发送'}
          aria-label={loading ? '停止生成' : '发送'}
        >
          {loading ? <StopCircle size={18} /> : <CornerDownLeft size={18} />}
        </button>
      </div>
    </section>
  )
}
