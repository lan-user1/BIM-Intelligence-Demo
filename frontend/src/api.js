// 前端 API 访问层：统一处理请求路径、错误消息和 SSE 流式响应。

/**
 * 读取后端返回的错误信息。后端可能使用 detail 或 message 字段。
 */
async function parseError(response) {
  try {
    const payload = await response.json()
    return payload.detail || payload.message || `请求失败 (${response.status})`
  } catch {
    return `请求失败 (${response.status})`
  }
}

/**
 * 发送普通 JSON 请求，并在 HTTP 状态异常时统一抛出 Error。
 */
async function request(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  return response.json()
}

// 健康检查用于判断后端及 AI 服务是否可用。
export function getHealth() {
  return request('/api/health')
}

// 获取后端扫描到的 IFC/RVT 模型列表。
export function getModels() {
  return request('/api/models')
}

// 获取单个模型的完整解析结果。
export function getModel(modelId) {
  return request(`/api/models/${encodeURIComponent(modelId)}`)
}

/**
 * 分页查询模型构件，并过滤空查询参数，避免生成无意义的 URL。
 */
export function getElements(modelId, params = {}) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, value)
    }
  })
  return request(
    `/api/models/${encodeURIComponent(modelId)}/elements?${query.toString()}`,
  )
}

// 获取本地知识库索引状态。
export function getKnowledgeStatus() {
  return request('/api/knowledge/status')
}

/**
 * 以 multipart/form-data 上传 IFC 或 RVT 文件。
 */
export async function uploadModel(file) {
  const body = new FormData()
  body.append('file', file)
  return request('/api/models/upload', {
    method: 'POST',
    body,
  })
}

/**
 * 消费后端 SSE 流。
 *
 * 后端按 `event: <name>\ndata: <json>\n\n` 发送事件；网络分片不保证刚好
 * 落在事件边界上，因此这里会保留未完整读取的缓冲区，直到下一次读入后再解析。
 */
export async function streamChat(payload, handlers, signal) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (!response.ok) {
    throw new Error(await parseError(response))
  }
  if (!response.body) {
    throw new Error('浏览器不支持流式响应。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // 空行是 SSE 事件分隔符，最后一段可能尚未结束，继续留在缓冲区。
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''

    for (const block of blocks) {
      let eventName = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          data += line.slice(5).trim()
        }
      }
      if (!data) continue

      let parsed
      try {
        parsed = JSON.parse(data)
      } catch {
        // 忽略格式不完整或非 JSON 的事件，保持流连接继续读取。
        continue
      }

      // 根据事件类型把结果分发给调用方的对应回调。
      if (eventName === 'meta') handlers.onMeta?.(parsed)
      if (eventName === 'token') handlers.onToken?.(parsed.text || '')
      if (eventName === 'done') handlers.onDone?.(parsed)
      if (eventName === 'error') {
        throw new Error(parsed.message || '模型回答中断。')
      }
    }
  }
}
