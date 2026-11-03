// 统一的 API 客户端。生产部署时前端与后端同源（nginx 反代 /api），无需配置 baseURL。
const BASE = ''

async function request(path, options = {}) {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      detail = body.detail || JSON.stringify(body)
    } catch (_) { /* 忽略非 JSON 响应 */ }
    throw new Error(detail)
  }
  if (resp.status === 204) return null
  return resp.json()
}

const qs = (params) => {
  const p = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.append(k, v)
  })
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const api = {
  platforms: () => request('/api/v1/platforms'),
  // 界面上直接接入新平台（零重启）
  connectPlatform: (body) =>
    request('/api/v1/platforms/connect', { method: 'POST', body: JSON.stringify(body) }),
  savedPlatforms: () => request('/api/v1/platforms/saved'),
  // 默认软停用（保留凭证，之后能一键重新启用）；purge=true 才是真删
  disconnectPlatform: (platform, purge = false) =>
    request(`/api/v1/platforms/${platform}/connect${purge ? '?purge=true' : ''}`, {
      method: 'DELETE',
    }),
  enablePlatform: (platform) =>
    request(`/api/v1/platforms/${platform}/enable`, { method: 'POST' }),
  threads: (params) => request('/api/v1/inbox/threads' + qs(params)),
  messages: (params) => request('/api/v1/inbox' + qs(params)),
  // quoteId：引用回复时传被引用消息的 id，后端会把它拼成引用块带到对方平台
  reply: (id, text, quoteId = null) =>
    request(`/api/v1/inbox/${id}/reply`, {
      method: 'POST',
      body: JSON.stringify(quoteId ? { text, quote_id: quoteId } : { text }),
    }),
  // 从统一入口给对方发文件（multipart，不能用 request 的 JSON 头）
  replyFile: async (id, file) => {
    const fd = new FormData()
    fd.append('file', file, file.name)
    const resp = await fetch(`${BASE}/api/v1/inbox/${id}/reply-file`, { method: 'POST', body: fd })
    if (!resp.ok) {
      let detail = resp.statusText
      try {
        const body = await resp.json()
        detail = body.detail || JSON.stringify(body)
      } catch (_) { /* 忽略非 JSON */ }
      throw new Error(detail)
    }
    return resp.json()
  },
  // 人工改紧急程度（覆盖 AI 判断）
  setPriority: (id, priority) =>
    request(`/api/v1/inbox/${id}/priority`, {
      method: 'PATCH',
      body: JSON.stringify({ priority }),
    }),
  createTodo: (id, payload) =>
    request(`/api/v1/inbox/${id}/todo`, { method: 'POST', body: JSON.stringify(payload) }),
  setStatus: (id, status, notify = true) =>
    request(`/api/v1/inbox/${id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status, notify }),
    }),
  markRead: (platform, chat_id, message_ids = null) =>
    request('/api/v1/inbox/read', {
      method: 'POST',
      body: JSON.stringify({ platform, chat_id, message_ids }),
    }),
  // —— 批量操作 ——
  bulkRead: (ids) =>
    request('/api/v1/inbox/bulk/read', { method: 'POST', body: JSON.stringify({ ids }) }),
  bulkStatus: (ids, status) =>
    request('/api/v1/inbox/bulk/status', {
      method: 'PATCH',
      body: JSON.stringify({ ids, status }),
    }),
  bulkPriority: (ids, priority) =>
    request('/api/v1/inbox/bulk/priority', {
      method: 'PATCH',
      body: JSON.stringify({ ids, priority }),
    }),
  summarize: (id) => request(`/api/v1/inbox/${id}/summarize`, { method: 'POST' }),
  draft: (id) => request(`/api/v1/inbox/${id}/draft`, { method: 'POST' }),
  forward: (id, target_platform, target_chat) =>
    request(`/api/v1/inbox/${id}/forward${qs({ target_platform, target_chat })}`, { method: 'POST' }),
  todos: (params) => request('/api/v1/todos' + qs(params)),
  updateTodo: (id, status) =>
    request(`/api/v1/todos/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  stats: () => request('/api/v1/stats/overview'),
  health: () => request('/healthz'),

  // ---- 用户身份（OAuth）----
  feishuAuthUrl: () => request('/api/v1/auth/feishu/url'),
  accounts: () => request('/api/v1/auth/accounts'),
  accountChats: (id) => request(`/api/v1/auth/accounts/${id}/chats`),
  syncAccount: (id, chats = 20, perChat = 50) =>
    request(`/api/v1/auth/accounts/${id}/sync?chats=${chats}&per_chat=${perChat}`, { method: 'POST' }),
  refreshAccount: (id) => request(`/api/v1/auth/accounts/${id}/refresh`, { method: 'POST' }),
  removeAccount: (id) => request(`/api/v1/auth/accounts/${id}`, { method: 'DELETE' }),
}

/**
 * 附件地址：走后端代理（平台凭证只存在服务器上）。
 * download=true 时强制下载，否则图片内联预览。
 */
export function attachmentUrl(messageId, index, download = false) {
  return `${BASE}/api/v1/inbox/${messageId}/attachments/${index}${download ? '?download=true' : ''}`
}

export const PLATFORM_LABEL = { feishu: '飞书', dingtalk: '钉钉', telegram: 'Telegram', email: '邮箱' }
export const PLATFORM_COLOR = { feishu: 'blue', dingtalk: 'geekblue', telegram: 'cyan', email: 'orange' }
export const STATUS_LABEL = {
  received: '待处理',
  claimed: '处理中',
  replied: '已回复',
  closed: '已关闭',
  sent: '已发送',
}
export const STATUS_COLOR = {
  received: 'orange',
  claimed: 'processing',
  replied: 'success',
  closed: 'default',
  sent: 'blue',
}
export const PRIORITY_COLOR = { urgent: 'red', normal: 'default', low: 'default' }
