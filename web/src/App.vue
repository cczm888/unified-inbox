<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message as antMessage } from 'ant-design-vue'
import dayjs from 'dayjs'
import {
  api,
  attachmentUrl,
  PLATFORM_LABEL,
  PLATFORM_COLOR,
  STATUS_LABEL,
  STATUS_COLOR,
  PRIORITY_COLOR,
} from './api'

const platforms = ref([])
const threads = ref([])
const messages = ref([])
const todos = ref([])
const stats = ref(null)
const activePlatform = ref(undefined)
const activeThread = ref(null)
const selected = ref(null)
const replyText = ref('')
const aiText = ref('')
const loading = ref(false)
const sending = ref(false)
const sendingFile = ref(false)
const reflowOn = ref(false) // 改状态时是否给对方发回执（默认关，避免把「[统一收件箱] 已标记为…」刷进对话）
const showEcho = ref(false) // 是否显示系统回执消息（默认关）
const forwardKey = ref('')
const keyword = ref('')

const msgArea = ref(null)
let es = null

// ---------- 中文标签（界面上不允许出现英文枚举值）----------
const PRIORITY_LABEL = { urgent: '紧急', normal: '普通', low: '低' }
const TODO_STATUS_LABEL = { pending: '待处理', in_progress: '进行中', done: '已完成', cancelled: '已取消' }
const TODO_STATUS_COLOR = { pending: 'orange', in_progress: 'blue', done: 'green', cancelled: 'default' }

const filteredThreads = computed(() =>
  activePlatform.value ? threads.value.filter((t) => t.platform === activePlatform.value) : threads.value
)

const pendingTotal = computed(() => threads.value.reduce((sum, t) => sum + Number(t.pending || 0), 0))
const unreadTotal = computed(() => threads.value.reduce((sum, t) => sum + Number(t.unread || 0), 0))
const urgentTotal = computed(() => threads.value.reduce((sum, t) => sum + Number(t.urgent || 0), 0))

// 系统自动回执（「[统一收件箱] 该消息已标记为…」这类自己发出去的通知）默认不显示：
// 它们是我自己的操作回声，混在对话里只会把真正的内容顶走。需要排查时可打开开关。
const isSystemEcho = (m) =>
  m.direction === 'out' && (m.text || '').trimStart().startsWith('[统一收件箱]')
const echoCount = computed(() => messages.value.filter(isSystemEcho).length)
const visibleMessages = computed(() =>
  showEcho.value ? messages.value : messages.value.filter((m) => !isSystemEcho(m)),
)

// 把消息流按"今天 / 昨天 / 具体日期"分隔，长会话才看得清
const timeline = computed(() => {
  const out = []
  let dayKey = ''
  for (const m of visibleMessages.value) {
    const d = dayjs(m.created_at)
    const k = d.format('YYYY-MM-DD')
    if (k !== dayKey) {
      dayKey = k
      out.push({ kind: 'day', key: 'd' + k, label: dayLabel(d) })
    }
    out.push({ kind: 'msg', key: 'm' + m.id, m })
  }
  return out
})

// 转发目标：直接从已有会话里挑，普通员工不该被要求手填 chat_id
const forwardTargets = computed(() =>
  threads.value
    .filter((t) => !(activeThread.value && t.chat_id === activeThread.value.chat_id && t.platform === activeThread.value.platform))
    .map((t) => ({
      value: `${t.platform}|${t.chat_id}`,
      label: `${PLATFORM_LABEL[t.platform] || t.platform} · ${t.chat_title || t.chat_id}`,
      platform: t.platform,
      chat_id: t.chat_id,
    }))
)

const fmt = (v) => (v ? dayjs(v).format('MM-DD HH:mm') : '')

function dayLabel(d) {
  const today = dayjs().startOf('day')
  if (d.isSame(today, 'day')) return '今天'
  if (d.isSame(today.subtract(1, 'day'), 'day')) return '昨天'
  return d.format('MM 月 DD 日')
}

function notify(fn, fallback) {
  try {
    fn()
  } catch (_) {
    antMessage.info(fallback)
  }
}

async function scrollToBottom() {
  await nextTick()
  const el = msgArea.value
  if (el) el.scrollTop = el.scrollHeight
}

// ---------- 加载 ----------
async function loadPlatforms() {
  try {
    const data = await api.platforms()
    platforms.value = data.platforms
  } catch (e) {
    notify(() => antMessage.error('后端未连接：' + e.message), e.message)
  }
}

async function loadThreads() {
  try {
    threads.value = await api.threads({ limit: 80, q: keyword.value.trim() || undefined })
  } catch (e) {
    console.warn('加载会话失败', e)
  }
}

// 搜索：打字时防抖，不用每敲一个字母就打一次接口
let searchTimer = null
function onKeywordChange() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(loadThreads, 350)
}

// 把命中的关键词标黄，员工一眼看到"为什么这条被搜出来"
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}
function highlight(text) {
  const safe = escapeHtml(text)
  const kw = keyword.value.trim()
  if (!kw) return safe
  const re = new RegExp(kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
  return safe.replace(re, (m) => `<mark>${m}</mark>`)
}

async function loadStats() {
  try {
    stats.value = await api.stats()
  } catch (_) { /* 统计失败不影响主流程 */ }
}

async function loadTodos() {
  try {
    todos.value = await api.todos({})
  } catch (_) { /* 同上 */ }
}

async function openThread(t) {
  activeThread.value = { platform: t.platform, chat_id: t.chat_id, title: t.chat_title || t.chat_id }
  aiText.value = ''
  await loadMessages()
  // 打开会话默认选最后一条待处理的消息；都处理完了就选最后一条"收到的"，
  // 而不是选中自己发出去的那条（否则右手面板显示"来自 我"，没意义）
  const pending = [...messages.value].reverse().find((m) => m.status === 'received' || m.status === 'claimed')
  const lastIn = [...messages.value].reverse().find((m) => m.direction === 'in')
  selected.value = pending || lastIn || messages.value[messages.value.length - 1] || null
  // 打开即已读：看过就不该再亮红点（但"待处理"状态保持不变）
  markThreadRead()
}

// 手机端从会话返回列表（第 5 项自适应的一部分）
function backToList() {
  activeThread.value = null
  selected.value = null
  messages.value = []
}

// ---------------- 用户身份账号（OAuth 接入） ----------------
const accountsOpen = ref(false)
const userAccounts = ref([])
const accountsLoading = ref(false)
const syncingId = ref(null)
const chatsLoadingId = ref(null)
const visibleChats = ref([])
const syncResult = ref('')

function fmtExpire(iso) {
  if (!iso) return '—'
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return '已过期'
  const m = Math.round(ms / 60000)
  return m > 90 ? `${Math.round(m / 60)} 小时后过期` : `${m} 分钟后过期`
}

async function loadAccounts() {
  accountsLoading.value = true
  try {
    userAccounts.value = await api.accounts()
  } catch (e) {
    antMessage.error('读取账号失败：' + e.message)
  } finally {
    accountsLoading.value = false
  }
}

function openAccounts() {
  accountsOpen.value = true
  visibleChats.value = []
  syncResult.value = ''
  loadAccounts()
}

async function startFeishuAuth() {
  try {
    const { url } = await api.feishuAuthUrl()
    window.open(url, '_blank', 'width=900,height=760')
    antMessage.info('已打开飞书授权页，同意后回来点「刷新状态」')
    // 授权完成后用户回来时自动刷一下
    setTimeout(loadAccounts, 8000)
  } catch (e) {
    antMessage.error('获取授权链接失败：' + e.message)
  }
}

// ---------- 界面上直接接入新平台（零重启） ----------
const newPlat = reactive({ platform: 'email', app_id: '', secret: '', imap_host: '', smtp_host: '' })
const connecting = ref(false)

const PLAT_HINTS = {
  email: {
    idLabel: '邮箱地址',
    idPlaceholder: 'you@qq.com',
    secretLabel: '授权码',
    secretPlaceholder: '上一步拿到的 16 位授权码',
    secretExtra: '不是邮箱登录密码，是上一步生成的那 16 位授权码',
    tip: '主机名留空会按邮箱后缀自动推断（qq/163/gmail/outlook 都认）。',
    afterConnect: '接入后系统每 30 秒查一次新邮件，收到的会出现在左侧列表，回复走 SMTP 原路发回。',
  },
  telegram: {
    idLabel: '备注名（随便填）',
    idPlaceholder: '我的 Telegram',
    secretLabel: 'Bot Token',
    secretPlaceholder: '123456789:AAH...',
    secretExtra: '形如 数字:字母串，不是 @用户名',
    tip: '接好后在 Telegram 里给机器人发任意消息即可。',
    afterConnect: '接入后机器人开始长轮询，给它发消息会立刻出现在左侧列表。',
  },
  feishu: {
    idLabel: 'App ID',
    idPlaceholder: 'cli_xxxxxxxxxxxx',
    secretLabel: 'App Secret',
    secretPlaceholder: '开放平台 → 凭证与基础信息',
    secretExtra: '32 位字母数字，不是登录密码',
    tip: '需要先在开放平台开好长连接事件订阅和 im:message 等权限并发布版本。',
    afterConnect: '接入后走 WebSocket 长连接，不用公网 IP，消息实时进来。',
  },
  dingtalk: {
    idLabel: 'Client ID（AppKey）',
    idPlaceholder: 'dingxxxxxxxx',
    secretLabel: 'Client Secret（AppSecret）',
    secretPlaceholder: '开放平台 → 凭证与基础信息',
    secretExtra: '48 位，不是登录密码',
    tip: '需要企业管理员身份，事件订阅要选 Stream 模式并验证连接通道。',
    afterConnect: '接入后走 Stream 长连接，机器人收到的消息会进入统一收件箱。',
  },
}

const platHint = computed(() => PLAT_HINTS[newPlat.platform] || PLAT_HINTS.email)

// 按邮箱后缀给出「去哪儿拿授权码」的直达入口
const MAIL_HELP = {
  'qq.com': { url: 'https://mail.qq.com', label: '打开 QQ 邮箱 → 设置 → 账户 → 开启 IMAP/SMTP 服务' },
  'foxmail.com': { url: 'https://mail.qq.com', label: '打开 QQ 邮箱 → 设置 → 账户 → 开启 IMAP/SMTP 服务' },
  '163.com': { url: 'https://mail.163.com', label: '打开 163 邮箱 → 设置 → POP3/SMTP/IMAP → 开启 IMAP/SMTP' },
  '126.com': { url: 'https://mail.126.com', label: '打开 126 邮箱 → 设置 → POP3/SMTP/IMAP → 开启 IMAP/SMTP' },
  'yeah.net': { url: 'https://mail.yeah.net', label: '打开 yeah 邮箱 → 设置 → POP3/SMTP/IMAP' },
  'gmail.com': { url: 'https://myaccount.google.com/apppasswords', label: '打开 Google「应用专用密码」页面生成 16 位密码' },
  'outlook.com': { url: 'https://account.microsoft.com/security', label: '打开微软账户安全页 → 高级安全选项 → 应用密码' },
  'hotmail.com': { url: 'https://account.microsoft.com/security', label: '打开微软账户安全页 → 高级安全选项 → 应用密码' },
  'live.com': { url: 'https://account.microsoft.com/security', label: '打开微软账户安全页 → 高级安全选项 → 应用密码' },
}

const mailHelp = computed(() => {
  const addr = (newPlat.app_id || '').trim().toLowerCase()
  const domain = addr.includes('@') ? addr.split('@').pop() : ''
  if (domain && MAIL_HELP[domain]) return MAIL_HELP[domain]
  return {
    url: 'https://mail.qq.com',
    label: '先在上一格填好邮箱地址，这里会变成对应邮箱的设置入口',
  }
})

// 每个平台「去哪儿拿凭证」的直达链接
const PLAT_HELP = {
  telegram: { url: 'https://t.me/BotFather', label: '打开 @BotFather → 发 /newbot 拿 token' },
  feishu: { url: 'https://open.feishu.cn/app', label: '打开飞书开放平台 → 凭证与基础信息' },
  dingtalk: { url: 'https://open-dev.dingtalk.com/fe/app', label: '打开钉钉开放平台 → 凭证与基础信息' },
}

const helpLink = computed(() =>
  newPlat.platform === 'email' ? mailHelp.value : PLAT_HELP[newPlat.platform] || null,
)

// ---------- 分步引导：每个平台到底该点哪儿 ----------
const guideOpen = ref(['g'])

const MAIL_GUIDES = {
  qq: {
    title: 'QQ 邮箱怎么拿授权码',
    url: 'https://mail.qq.com',
    linkText: '打开 QQ 邮箱',
    steps: [
      '登录 QQ 邮箱，点右上角「设置」',
      '左侧「账号」分组里点「账号与安全」',
      '找到「IMAP/SMTP 服务」，点右侧「开启」（已开启则点「生成授权码」）',
      '按提示用手机发短信「配置邮件客户端」到 1069070069，回来点「我已发送」',
      '弹窗里那串 16 位字母数字就是授权码，复制它',
    ],
  },
  '163': {
    title: '163 / 126 邮箱怎么拿授权码',
    url: 'https://mail.163.com',
    linkText: '打开 163 邮箱',
    steps: [
      '登录邮箱，点顶部「设置」→「POP3/SMTP/IMAP」',
      '在「IMAP/SMTP服务」那一行点「开启」',
      '按提示完成手机验证（扫码或短信）',
      '弹出的「客户端授权密码」就是授权码，点「复制」',
    ],
  },
  gmail: {
    title: 'Gmail 怎么拿应用专用密码',
    url: 'https://myaccount.google.com/apppasswords',
    linkText: '打开应用专用密码页面',
    steps: [
      '先确认 Google 账号已开启「两步验证」（没开这一页进不去）',
      '打开上面的链接，应用名随便填（如「统一收件箱」）',
      '点「创建」，Google 会显示 16 位密码（形如 abcd efgh ijkl mnop）',
      '把这 16 位连着写、去掉空格，粘到下面',
    ],
  },
  outlook: {
    title: 'Outlook / Hotmail 怎么拿应用密码',
    url: 'https://account.microsoft.com/security',
    linkText: '打开微软账户安全页',
    steps: [
      '先开启「双重验证」',
      '在安全页找到「高级安全选项」→「应用密码」',
      '点「创建新的应用密码」，复制生成的密码',
    ],
  },
  generic: {
    title: '自建/企业邮箱怎么拿授权码',
    url: 'https://mail.qq.com',
    linkText: '（示例）打开邮箱',
    steps: [
      '登录你的邮箱网页版，进「设置」找「POP3/IMAP/SMTP」或「客户端授权」',
      '开启 IMAP 服务，通常会要求短信或扫码验证',
      '记下服务器地址（常见 imap.你的域名 / smtp.你的域名，端口 993 / 465）',
      '把服务器地址填到下面的「IMAP / SMTP 主机」，不然自动推断可能失败',
    ],
  },
}

const PLAT_GUIDES = {
  telegram: {
    title: 'Telegram 怎么拿 Bot Token',
    url: 'https://t.me/BotFather',
    linkText: '打开 @BotFather',
    steps: [
      '在 Telegram 里搜索 @BotFather 并打开对话',
      '发送 /newbot，按提示起一个名字（如「统一收件箱」）',
      '再起一个用户名，必须以 bot 结尾（如 unify_inbox_bot）',
      '它会回一段 「Use this token to access the HTTP API:」 下面的那串 token',
      '那串形如 123456789:AAH... 的就是密钥，粘到下面',
    ],
  },
  feishu: {
    title: '飞书怎么拿 App ID / Secret',
    url: 'https://open.feishu.cn/app',
    linkText: '打开飞书开放平台',
    steps: [
      '进入开放平台 →「创建企业自建应用」',
      '左侧「凭证与基础信息」里复制 App ID 和 App Secret',
      '「添加应用能力」里加上「机器人」',
      '「权限管理」开通 im:message、im:message.p2p_msg:readonly、im:resource、im:chat:readonly',
      '「事件与回调」订阅方式选「长连接」，添加事件 im.message.receive_v1',
      '「版本管理与发布」创建版本并发布，配置才生效',
    ],
  },
  dingtalk: {
    title: '钉钉怎么拿 Client ID / Secret',
    url: 'https://open-dev.dingtalk.com/fe/app',
    linkText: '打开钉钉开放平台',
    steps: [
      '需要企业管理员身份才能创建企业内部应用',
      '创建应用后进「凭证与基础信息」，复制 Client ID（原 AppKey）和 Client Secret',
      '「添加应用能力」里加上「机器人」',
      '「事件订阅」推送方式选「Stream 模式」，并验证连接通道（这一步要管理员权限）',
    ],
  },
}

const guide = computed(() => {
  if (newPlat.platform !== 'email') {
    return PLAT_GUIDES[newPlat.platform] || MAIL_GUIDES.generic
  }
  const addr = (newPlat.app_id || '').trim().toLowerCase()
  const d = addr.includes('@') ? addr.split('@').pop() : ''
  if (d === 'qq.com' || d === 'foxmail.com') return MAIL_GUIDES.qq
  if (d === '163.com' || d === '126.com' || d === 'yeah.net') return MAIL_GUIDES['163']
  if (d === 'gmail.com') return MAIL_GUIDES.gmail
  if (['outlook.com', 'hotmail.com', 'live.com'].includes(d)) return MAIL_GUIDES.outlook
  return MAIL_GUIDES.generic
})

// 输入格式的即时体检（只在看起来像填错时才红）
const idHint = computed(() => {
  const v = (newPlat.app_id || '').trim()
  if (!v) return ''
  if (newPlat.platform === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) {
    return '这不像一个邮箱地址，应该是 xxx@qq.com 这种'
  }
  if (newPlat.platform === 'telegram' && v.includes('@')) {
    return '这里填 token（含冒号的那串），不是机器人用户名'
  }
  if (newPlat.platform === 'feishu' && /@/.test(v)) {
    return '这里填 App ID（cli_ 开头），不是登录邮箱'
  }
  return ''
})
const idInvalid = computed(() => idHint.value !== '')

async function doConnect() {
  if (!newPlat.app_id.trim() || !newPlat.secret.trim()) {
    antMessage.warning('② 和 ③ 两格都要填：账号 + 密钥/授权码')
    return
  }
  if (idInvalid.value) {
    antMessage.warning(idHint.value)
    return
  }
  if (newPlat.platform === 'email' && newPlat.secret.trim().length < 8) {
    antMessage.warning('授权码通常 16 位，检查一下是不是只复制了一半')
    return
  }
  connecting.value = true
  try {
    const body = {
      platform: newPlat.platform,
      app_id: newPlat.app_id.trim(),
      secret: newPlat.secret.trim(),
      label: newPlat.app_id.trim(),
    }
    if (newPlat.platform === 'email') {
      body.extra = {
        imap_host: newPlat.imap_host.trim() || undefined,
        smtp_host: newPlat.smtp_host.trim() || undefined,
      }
    }
    const r = await api.connectPlatform(body)
    antMessage.success(`已接入：${r.label || r.platform}（${r.ready ? '在线' : '离线'}）`)
    newPlat.app_id = ''
    newPlat.secret = ''
    await Promise.all([loadPlatforms(), loadAccounts()])
  } catch (e) {
    antMessage.error('接入失败：' + e.message, 8)
  } finally {
    connecting.value = false
  }
}

async function syncAcc(a) {
  syncingId.value = a.id
  syncResult.value = ''
  try {
    const r = await api.syncAccount(a.id)
    syncResult.value = `同步完成：可见 ${r.chats_visible} 个会话，同步了 ${r.chats_synced} 个，写入 ${r.messages_upserted} 条消息`
    await Promise.all([loadThreads(), loadStats(), loadAccounts()])
  } catch (e) {
    antMessage.error('同步失败：' + e.message)
  } finally {
    syncingId.value = null
  }
}

async function showChats(a) {
  chatsLoadingId.value = a.id
  visibleChats.value = []
  try {
    visibleChats.value = await api.accountChats(a.id)
    antMessage.success(`这个账号能看到 ${visibleChats.value.length} 个会话`)
  } catch (e) {
    antMessage.error('读取会话失败：' + e.message)
  } finally {
    chatsLoadingId.value = null
  }
}

async function refreshAcc(a) {
  try {
    const r = await api.refreshAccount(a.id)
    antMessage.info(r.ok ? 'token 已续期' : '续期失败，需要重新授权')
    await loadAccounts()
  } catch (e) {
    antMessage.error(e.message)
  }
}

async function removeAcc(a) {
  try {
    await api.removeAccount(a.id)
    antMessage.success('已移除')
    await loadAccounts()
  } catch (e) {
    antMessage.error(e.message)
  }
}

async function markThreadRead() {  const t = activeThread.value
  if (!t) return
  const hasUnread = threads.value.some(
    (x) => x.platform === t.platform && x.chat_id === t.chat_id && Number(x.unread) > 0
  )
  if (!hasUnread) return
  try {
    await api.markRead(t.platform, t.chat_id)
    // 本地同步把气泡上的未读点去掉，不用等下一次拉取
    const stamp = new Date().toISOString()
    messages.value = messages.value.map((m) =>
      m.direction === 'in' && !m.read_at ? { ...m, read_at: stamp } : m
    )
    await loadThreads()
  } catch (e) {
    console.warn('标记已读失败', e)
  }
}

async function loadMessages() {
  if (!activeThread.value) return
  loading.value = true
  try {
    const rows = await api.messages({
      platform: activeThread.value.platform,
      chat_id: activeThread.value.chat_id,
      limit: 100,
    })
    messages.value = rows.slice().reverse() // 后端倒序，界面按正序排成对话流
    if (selected.value) {
      const fresh = messages.value.find((m) => m.id === selected.value.id)
      selected.value = fresh || null
    }
    await scrollToBottom()
  } catch (e) {
    notify(() => antMessage.error('加载消息失败：' + e.message), e.message)
  } finally {
    loading.value = false
  }
}

function pick(m) {
  selected.value = m
  aiText.value = ''
}

// ---------- 处理动作 ----------
async function sendReply() {
  if (!selected.value || !replyText.value.trim() || sending.value) return
  sending.value = true
  try {
    const res = await api.reply(selected.value.id, replyText.value.trim())
    notify(() => antMessage.success(`已发送到${PLATFORM_LABEL[res.platform] || res.platform}`), '已发送')
    replyText.value = ''
    await loadMessages()
    await loadThreads()
    await loadStats()
    await scrollToBottom()
  } catch (e) {
    notify(() => antMessage.error('发送失败：' + e.message), e.message)
  } finally {
    sending.value = false
  }
}

async function makeTodo() {
  if (!selected.value) return
  try {
    const res = await api.createTodo(selected.value.id, {})
    notify(() => antMessage.success('已转为待办 #' + res.todo_id), '已转待办')
    await loadMessages()
    await loadTodos()
  } catch (e) {
    notify(() => antMessage.error(e.message), e.message)
  }
}

async function setStatus(status) {
  if (!selected.value) return
  const send = reflowOn.value
  try {
    await api.setStatus(selected.value.id, status, send)
    notify(
      () =>
        antMessage.success(
          send
            ? `已标记为「${STATUS_LABEL[status]}」，并把回执发给了对方`
            : `已标记为「${STATUS_LABEL[status]}」`,
        ),
      '已更新',
    )
    await loadMessages()
    await loadThreads()
    await loadStats()
  } catch (e) {
    notify(() => antMessage.error(e.message), e.message)
  }
}

async function doSummarize() {
  if (!selected.value) return
  try {
    const res = await api.summarize(selected.value.id)
    aiText.value = `${res.summary}\n\n优先级：${PRIORITY_LABEL[res.priority] || res.priority}（${res.source === 'llm' ? '大模型' : '规则降级'}）`
    await loadMessages()
  } catch (e) {
    notify(() => antMessage.error(e.message), e.message)
  }
}

// 人工改处理状态（点气泡标签或右侧下拉都走这里；默认不回流，避免刷屏）
async function changeStatus(msg, status) {
  if (!msg || msg.status === status) return
  const prev = msg.status
  msg.status = status // 乐观更新
  try {
    await api.setStatus(msg.id, status, false)
    antMessage.success(`状态已改为「${STATUS_LABEL[status] || status}」`)
    await Promise.all([loadMessages(), loadThreads(), loadStats()])
  } catch (e) {
    msg.status = prev
    antMessage.error('改状态失败：' + e.message)
  }
}

// 人工改紧急程度（AI 判错了可以自己改回来）
async function changePriority(msg, priority) {  if (!msg || msg.priority === priority) return
  const prev = msg.priority
  msg.priority = priority // 乐观更新，失败再回滚
  try {
    await api.setPriority(msg.id, priority)
    antMessage.success(`紧急程度已改为「${PRIORITY_LABEL[priority] || priority}」`)
    await Promise.all([loadMessages(), loadThreads(), loadStats()])
  } catch (e) {
    msg.priority = prev
    antMessage.error('修改失败：' + e.message)
  }
}

// 气泡上的「紧急」标签点 × 直接取消
async function clearPriority(msg) {
  await changePriority(msg, 'normal')
}

// —— 批量操作：勾几条消息一起处理（只对收到的消息，发出的消息状态是投递结果） ——
const checkedIds = ref(new Set())
const checkedList = computed(() => Array.from(checkedIds.value))
const inboundMessages = computed(() => messages.value.filter((m) => m.direction === 'in'))
const allChecked = computed(
  () => inboundMessages.value.length > 0 && checkedIds.value.size === inboundMessages.value.length,
)

function toggleCheck(m) {
  const s = new Set(checkedIds.value)
  if (s.has(m.id)) s.delete(m.id)
  else s.add(m.id)
  checkedIds.value = s
}

function checkAll() {
  checkedIds.value = allChecked.value ? new Set() : new Set(inboundMessages.value.map((m) => m.id))
}

function clearChecked() {
  checkedIds.value = new Set()
}

async function bulkRead() {
  const ids = checkedList.value
  if (!ids.length) return
  try {
    const r = await api.bulkRead(ids)
    if (r.read > 0) antMessage.success(`已把 ${r.read} 条标为已读`)
    else antMessage.info(`选中的 ${r.inbound || ids.length} 条本来就是已读（点开会话时会自动标已读）`)
    clearChecked()
    await Promise.all([loadMessages(), loadThreads(), loadStats()])
  } catch (e) {
    antMessage.error('批量标已读失败：' + e.message)
  }
}

async function bulkStatus(status) {
  const ids = checkedList.value
  if (!ids.length) return
  try {
    const r = await api.bulkStatus(ids, status)
    antMessage.success(`已把 ${r.updated} 条改为「${STATUS_LABEL[status] || status}」`)
    clearChecked()
    await Promise.all([loadMessages(), loadThreads(), loadStats()])
  } catch (e) {
    antMessage.error('批量改状态失败：' + e.message)
  }
}

async function bulkPriority(priority) {
  const ids = checkedList.value
  if (!ids.length) return
  try {
    const r = await api.bulkPriority(ids, priority)
    antMessage.success(`已把 ${r.updated} 条紧急程度改为「${PRIORITY_LABEL[priority] || priority}」`)
    clearChecked()
    await Promise.all([loadMessages(), loadThreads(), loadStats()])
  } catch (e) {
    antMessage.error('批量改紧急程度失败：' + e.message)
  }
}

async function doDraft() {
  if (!selected.value) return
  try {
    const res = await api.draft(selected.value.id)
    replyText.value = res.draft
    notify(
      () => antMessage.info(res.source === 'llm' ? 'AI 草稿已生成，改完就能发' : '大模型未配置，已用模板兜底'),
      '草稿已生成'
    )
  } catch (e) {
    notify(() => antMessage.error(e.message), e.message)
  }
}

async function doForward() {
  const target = forwardTargets.value.find((x) => x.value === forwardKey.value)
  if (!selected.value || !target) {
    notify(() => antMessage.warning('先选一个转发目标会话'), '未选目标')
    return
  }
  try {
    await api.forward(selected.value.id, target.platform, target.chat_id)
    notify(() => antMessage.success(`已转发到 ${target.label}`), '已转发')
  } catch (e) {
    notify(() => antMessage.error(e.message), e.message)
  }
}

// 从统一入口给对方发文件（赛题要求：能向指定平台发文本与文件）
async function doSendFile(file) {
  if (!selected.value) return false
  if (file.size > 20 * 1024 * 1024) {
    antMessage.error('文件超过 20MB，请压缩后再发')
    return false
  }
  sendingFile.value = true
  try {
    const res = await api.replyFile(selected.value.id, file)
    antMessage.success(`已把「${res.filename}」发给对方`)
    await Promise.all([loadMessages(), loadThreads(), loadStats()])
  } catch (e) {
    antMessage.error('发文件失败：' + e.message)
  } finally {
    sendingFile.value = false
  }
  return false // 阻止 a-upload 自己上传
}

async function completeTodo(todo) {
  try {
    const res = await api.updateTodo(todo.id, 'done')
    notify(() => antMessage.success('已完成'), '已完成')
    await loadTodos()
    await loadMessages()
  } catch (e) {
    notify(() => antMessage.error(e.message), e.message)
  }
}

// Ctrl/Cmd + Enter 发送
function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    sendReply()
  }
}

// ---------- 实时推送 ----------
function connectSSE() {
  es = new EventSource('/api/v1/events')
  es.onmessage = (ev) => {
    try {
      const rows = JSON.parse(ev.data)
      if (!rows.length) return
      loadThreads()
      loadStats()
      const hit = activeThread.value
      if (hit && rows.some((r) => r.platform === hit.platform && r.chat_id === hit.chat_id)) {
        loadMessages()
      }
    } catch (_) { /* 心跳帧忽略 */ }
  }
  es.onerror = () => { /* 浏览器会自动重连 */ }
}

// 首屏加载完之前的 pendingTotal 变化是"加载"，不是"新消息"
const primed = ref(false)
const canNotify = ref(typeof window !== 'undefined' && 'Notification' in window)
const notifyOn = ref(canNotify.value && Notification.permission === 'granted')

function desktopNotify(n) {
  if (!notifyOn.value || !document.hidden) return
  try {
    const nt = new Notification('统一收件箱', {
      body: `收到 ${n} 条新消息`,
      tag: 'unified-inbox',
      silent: false,
    })
    nt.onclick = () => {
      window.focus()
      nt.close()
    }
  } catch (e) {
    console.warn('桌面通知失败', e)
  }
}

async function toggleNotify() {
  if (!canNotify.value) return
  if (notifyOn.value) {
    notifyOn.value = false
    antMessage.info('已关闭桌面通知')
    return
  }
  const p = await Notification.requestPermission()
  notifyOn.value = p === 'granted'
  antMessage.info(notifyOn.value ? '已开启桌面通知：窗口在后台时收到消息会弹出提示' : '浏览器拒绝了通知权限')
}

watch(pendingTotal, (n, o) => {
  if (!primed.value) return
  if (n > o) {
    antMessage.info(`收到 ${n - o} 条新消息`)
    desktopNotify(n - o)
  }
})

onMounted(async () => {
  await loadPlatforms()
  await Promise.all([loadThreads(), loadStats(), loadTodos()])
  primed.value = true
  connectSSE()
})

onBeforeUnmount(() => {
  if (es) es.close()
})
</script>

<template>
  <div class="app-root" @keydown="onKeydown">
    <header class="topbar">
      <div class="brand">
        统一消息收件箱
        <span class="sub">多平台连接器 · 赛题 A</span>
      </div>
      <div class="platforms">
        <a-tag v-for="p in platforms" :key="p.platform" :color="p.ready ? PLATFORM_COLOR[p.platform] : 'default'">
          {{ p.name }} {{ p.ready ? '在线' : '未就绪' }}
        </a-tag>
        <a-tag v-if="unreadTotal" color="blue">未读 {{ unreadTotal }}</a-tag>
        <a-tag v-if="urgentTotal" color="red">紧急 {{ urgentTotal }}</a-tag>
        <a-tag :color="pendingTotal ? 'orange' : 'default'">待处理 {{ pendingTotal }}</a-tag>
        <a-button size="small" @click="openAccounts">👤 账号（{{ userAccounts.length }}）</a-button>
        <a-button v-if="canNotify" size="small" type="text" @click="toggleNotify">
          {{ notifyOn ? '🔔 桌面通知已开' : '🔕 开启桌面通知' }}
        </a-button>
      </div>
    </header>

    <a-drawer
      v-model:open="accountsOpen"
      title="接入的账号"
      width="520"
      :body-style="{ padding: '16px 20px' }"
    >
      <a-alert type="info" show-icon style="margin-bottom: 14px">
        <template #message>应用身份 vs 用户身份</template>
        <template #description>
          <div style="line-height: 1.7; font-size: 12px">
            <b>应用身份（机器人）</b>：只收得到「发给机器人」的消息，实时、双向，本项目已打通。<br />
            <b>用户身份（授权登录）</b>：代表"你这个人"调用飞书接口——能读账号信息、你的群列表、群详情。
            <br />
            <span style="color: #d46b08">
              实测边界：飞书自建应用读「会话历史消息」需要 <code>im:message.*:get_as_user</code> 权限，
              个人租户的自建应用在权限目录里申请不到（接口返回 230027），所以用户身份目前只能拿目录类数据。
            </span>
          </div>
        </template>
      </a-alert>

      <a-space style="margin-bottom: 14px">
        <a-button type="primary" @click="startFeishuAuth">用飞书账号授权接入</a-button>
        <a-button :loading="accountsLoading" @click="loadAccounts">刷新状态</a-button>
      </a-space>

      <a-empty v-if="!userAccounts.length" description="还没有接入任何用户账号" />

      <a-card v-for="a in userAccounts" :key="a.id" size="small" style="margin-bottom: 12px">
        <template #title>
          <span style="font-size: 13px">
            {{ a.user_name || a.user_id }}
            <a-tag :color="a.status === 'active' ? 'green' : 'red'" style="margin-left: 6px">
              {{ a.status === 'active' ? '有效' : a.status === 'expired' ? '已过期' : '已失效' }}
            </a-tag>
          </span>
        </template>
        <div class="acc-meta">
          平台：{{ PLATFORM_LABEL[a.platform] || a.platform }} ·
          token {{ fmtExpire(a.token_expires_at) }}<br />
          权限：{{ a.scope || '—' }}<br />
          <span v-if="a.last_error" style="color: #cf1322">错误：{{ a.last_error }}</span>
          <span v-else-if="a.last_sync_at">上次同步：{{ fmt(a.last_sync_at) }}</span>
        </div>
        <div class="acc-actions">
          <a-button size="small" type="primary" ghost :loading="syncingId === a.id" @click="syncAcc(a)">
            同步我的会话
          </a-button>
          <a-button size="small" :loading="chatsLoadingId === a.id" @click="showChats(a)">
            查看可见会话
          </a-button>
          <a-button size="small" @click="refreshAcc(a)">续期</a-button>
          <a-popconfirm title="确定移除这个账号？" @confirm="removeAcc(a)">
            <a-button size="small" danger>移除</a-button>
          </a-popconfirm>
        </div>
        <div v-if="visibleChats.length && chatsLoadingId === null" class="chat-list">
          <div v-for="c in visibleChats" :key="c.chat_id" class="chat-row">
            <span class="chat-name">{{ c.name || c.chat_id }}</span>
            <span class="chat-meta">
              {{ c.chat_mode || c.chat_type || '' }}
              <template v-if="c.member_count">· {{ c.member_count }} 人</template>
            </span>
          </div>
        </div>
      </a-card>

      <a-divider v-if="syncResult" />
      <div v-if="syncResult" style="font-size: 12px; color: #389e0d">{{ syncResult }}</div>

      <a-divider>接入新平台</a-divider>
      <a-card size="small" style="margin-bottom: 12px">
        <template #title>
          <span style="font-size: 13px">三步接入 · 填完即生效，不用改 .env、不用重启</span>
        </template>

        <a-form layout="vertical" style="font-size: 12px">
          <a-form-item label="① 选择平台" style="margin-bottom: 10px">
            <a-select v-model:value="newPlat.platform" style="width: 100%">
              <a-select-option value="email">邮箱（IMAP 收 + SMTP 发）</a-select-option>
              <a-select-option value="telegram">Telegram Bot</a-select-option>
              <a-select-option value="feishu">飞书自建应用</a-select-option>
              <a-select-option value="dingtalk">钉钉企业内部应用</a-select-option>
            </a-select>
          </a-form-item>

          <a-collapse
            v-model:activeKey="guideOpen"
            ghost
            size="small"
            style="margin: 0 0 8px -12px"
            expand-icon-position="end"
          >
            <a-collapse-panel key="g">
              <template #header>
                <span style="font-size: 12px; color: #1677ff">
                  先看这里 · {{ guide.title }}（{{ guide.steps.length }} 步）
                </span>
              </template>
              <ol class="guide-list">
                <li v-for="(s, i) in guide.steps" :key="i">{{ s }}</li>
              </ol>
              <a :href="guide.url" target="_blank" rel="noopener" class="guide-link">
                {{ guide.linkText }} ↗
              </a>
            </a-collapse-panel>
          </a-collapse>

          <a-form-item :label="'② ' + platHint.idLabel" style="margin-bottom: 10px">
            <a-input
              v-model:value="newPlat.app_id"
              :placeholder="platHint.idPlaceholder"
              :status="idInvalid ? 'error' : ''"
            />
            <template v-if="idInvalid" #extra>
              <span style="color: #cf1322">{{ idHint }}</span>
            </template>
          </a-form-item>

          <a-form-item :label="'③ ' + platHint.secretLabel" style="margin-bottom: 10px">
            <a-input-password
              v-model:value="newPlat.secret"
              :placeholder="platHint.secretPlaceholder"
            />
            <template #extra>
              <span style="color: #8c8c8c">{{ platHint.secretExtra }}</span>
            </template>
          </a-form-item>

          <template v-if="newPlat.platform === 'email'">
            <a-form-item label="IMAP / SMTP 主机（选填，留空自动推断）" style="margin-bottom: 10px">
              <a-input v-model:value="newPlat.imap_host" placeholder="imap.qq.com" />
              <a-input v-model:value="newPlat.smtp_host" placeholder="smtp.qq.com" style="margin-top: 6px" />
            </a-form-item>
          </template>
        </a-form>

        <a-alert type="info" show-icon :message="platHint.afterConnect" style="margin-bottom: 10px" />
        <a-button type="primary" block :loading="connecting" @click="doConnect">接入</a-button>
        <div style="font-size: 12px; color: #8c8c8c; margin-top: 8px; line-height: 1.6">
          {{ platHint.tip }}
        </div>
      </a-card>
    </a-drawer>

    <div class="main" :class="{ reading: !!activeThread }">
      <!-- 左：统一收件箱（按平台+会话聚合） -->
      <aside class="col col-left">
        <div class="col-head">
          <span>收件箱</span>
          <a-select
            v-model:value="activePlatform"
            size="small"
            style="width: 110px"
            allow-clear
            placeholder="全部平台"
          >
            <a-select-option v-for="p in platforms" :key="p.platform" :value="p.platform">
              {{ p.name }}
            </a-select-option>
          </a-select>
        </div>
        <div class="search-bar">
          <a-input
            v-model:value="keyword"
            size="small"
            allow-clear
            placeholder="搜索人名、会话或消息内容"
            @press-enter="loadThreads"
            @change="onKeywordChange"
          >
            <template #suffix>
              <span class="search-hint">{{ keyword ? '回车' : '🔍' }}</span>
            </template>
          </a-input>
        </div>
        <div class="scroll flex-1">
          <a-empty
            v-if="!filteredThreads.length"
            :description="keyword ? '没搜到，换个词试试' : '还没有收到消息'"
          />
          <div
            v-for="t in filteredThreads"
            :key="t.platform + t.chat_id"
            class="thread-item"
            :class="{
              active:
                activeThread && activeThread.chat_id === t.chat_id && activeThread.platform === t.platform,
            }"
            @click="openThread(t)"
          >
            <div class="thread-top">
              <a-tag :color="PLATFORM_COLOR[t.platform]" style="margin-right: 6px">
                {{ PLATFORM_LABEL[t.platform] || t.platform }}
              </a-tag>
              <span class="thread-title">{{ t.chat_title || t.chat_id }}</span>
              <span v-if="Number(t.urgent) > 0" class="urgent-flag" title="有紧急待处理消息">急</span>
              <a-badge v-if="Number(t.unread) > 0" :count="Number(t.unread)" />
            </div>
            <div class="thread-last" v-html="highlight(t.last_text || '（无文本内容）')"></div>
            <div class="meta">
              {{ fmt(t.last_at) }} · 共 {{ t.total }} 条
              <span v-if="Number(t.pending) > 0" class="pending-hint">· {{ t.pending }} 条待处理</span>
            </div>
          </div>
        </div>
      </aside>

      <!-- 中：消息流 -->
      <section class="col col-mid">
        <div class="col-head">
          <a-button v-if="activeThread" size="small" class="back-btn" @click="backToList">← 列表</a-button>
          <span class="mid-title">
            {{ activeThread ? activeThread.title : '会话' }}
            <a-tag v-if="activeThread" :color="PLATFORM_COLOR[activeThread.platform]">
              {{ PLATFORM_LABEL[activeThread.platform] }}
            </a-tag>
          </span>
          <a-tooltip v-if="echoCount" title="我自己的操作回声（「[统一收件箱] 该消息已标记为…」这类），默认隐藏">
            <a-checkbox v-model:checked="showEcho" class="echo-toggle">
              系统回执 {{ echoCount }}
            </a-checkbox>
          </a-tooltip>
          <a-button size="small" :loading="loading" @click="loadMessages">刷新</a-button>
        </div>
        <div v-if="activeThread && visibleMessages.length" class="bulk-toolbar">
          <a-checkbox :checked="allChecked" @change="checkAll">
            {{ allChecked ? '取消全选' : '全选' }}
          </a-checkbox>
          <template v-if="checkedIds.size">
            <span class="bulk-count">已选 {{ checkedIds.size }} 条</span>
            <a-button size="small" @click="bulkRead">标已读</a-button>
            <a-dropdown :trigger="['click']">
              <a-button size="small">改状态 ▾</a-button>
              <template #overlay>
                <a-menu @click="({ key }) => bulkStatus(key)">
                  <a-menu-item key="received">待处理</a-menu-item>
                  <a-menu-item key="claimed">处理中</a-menu-item>
                  <a-menu-item key="replied">已回复</a-menu-item>
                  <a-menu-item key="closed">已关闭</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
            <a-dropdown :trigger="['click']">
              <a-button size="small">紧急度 ▾</a-button>
              <template #overlay>
                <a-menu @click="({ key }) => bulkPriority(key)">
                  <a-menu-item key="urgent">紧急</a-menu-item>
                  <a-menu-item key="normal">普通</a-menu-item>
                  <a-menu-item key="low">低</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
            <a class="bulk-clear" @click="clearChecked">取消选择</a>
          </template>
        </div>
        <div ref="msgArea" class="scroll flex-1 msg-area">
          <a-spin :spinning="loading" />
          <a-empty v-if="!activeThread" description="在左边选一个会话" />
          <a-empty v-else-if="!visibleMessages.length" description="这个会话还没有消息" />
          <template v-for="row in timeline" :key="row.key">
            <div v-if="row.kind === 'day'" class="day-sep">
              <span>{{ row.label }}</span>
            </div>
            <div
              v-else
              class="bubble"
              :class="{ out: row.m.direction === 'out', picked: selected && selected.id === row.m.id }"
              @click="pick(row.m)"
            >
              <div class="bubble-head">
                <a-checkbox
                  v-if="row.m.direction === 'in'"
                  class="bulk-check"
                  :checked="checkedIds.has(row.m.id)"
                  @click.stop
                  @change="toggleCheck(row.m)"
                />
                <span v-if="row.m.direction === 'in' && !row.m.read_at" class="unread-dot" title="未读"></span>
                <span class="who">{{ row.m.sender_name || (row.m.direction === 'out' ? '我' : PLATFORM_LABEL[row.m.platform]) }}</span>
                <a-dropdown :trigger="['click']">
                  <a-tag
                    :color="STATUS_COLOR[row.m.status]"
                    style="cursor: pointer; user-select: none"
                    title="点一下改状态"
                  >{{ STATUS_LABEL[row.m.status] || row.m.status }} ▾</a-tag>
                  <template #overlay>
                    <a-menu @click="({ key }) => changeStatus(row.m, key)">
                      <a-menu-item key="received">待处理</a-menu-item>
                      <a-menu-item key="claimed">处理中</a-menu-item>
                      <a-menu-item key="replied">已回复</a-menu-item>
                      <a-menu-item key="closed">已关闭</a-menu-item>
                    </a-menu>
                  </template>
                </a-dropdown>
                <a-tag
                  v-if="row.m.priority === 'urgent'"
                  color="red"
                  closable
                  title="点 × 取消紧急标记"
                  @close.stop="clearPriority(row.m)"
                >紧急</a-tag>
                <span class="meta" style="margin-left: auto">{{ fmt(row.m.created_at) }}</span>
              </div>
              <div class="msg-text">{{ row.m.text || '（非文本消息）' }}</div>
              <div v-if="row.m.attachments && row.m.attachments.length" class="att-list" @click.stop>
                <template v-for="(a, i) in row.m.attachments" :key="i">
                  <a
                    v-if="a.kind === 'image'"
                    :href="attachmentUrl(row.m.id, i, true)"
                    target="_blank"
                    rel="noreferrer"
                    class="att-img-wrap"
                    :title="'点击查看大图 / 右键另存'"
                  >
                    <img :src="attachmentUrl(row.m.id, i)" class="att-img" alt="图片附件" />
                  </a>
                  <a
                    v-else
                    :href="attachmentUrl(row.m.id, i, true)"
                    class="att-file"
                    :title="'点击下载 ' + (a.name || '附件')"
                  >
                    <span class="att-icon">📎</span>
                    <span class="att-name">{{ a.name || '附件' }}</span>
                    <span class="att-dl">下载</span>
                  </a>
                </template>
              </div>
            </div>
          </template>
        </div>
      </section>

      <!-- 右：处理面板 -->
      <aside class="col col-right">
        <a-tabs size="small" class="flex-1 tabs">
          <a-tab-pane key="handle" tab="处理">
            <a-empty v-if="!selected" description="点选上面的一条消息" />
            <div v-else class="panel">
              <div class="kv">
                <b>来自</b>
                {{ selected.direction === 'out' ? '我（本站发出）' : selected.sender_name || '未知用户' }}（{{
                  PLATFORM_LABEL[selected.platform]
                }}）
              </div>
              <div class="kv"><b>会话</b> {{ activeThread ? activeThread.title : selected.chat_id }}</div>
              <div class="kv">
                <b>状态</b>
                <a-select
                  :value="selected.status"
                  size="small"
                  style="width: 104px"
                  :options="[
                    { value: 'received', label: '待处理' },
                    { value: 'claimed', label: '处理中' },
                    { value: 'replied', label: '已回复' },
                    { value: 'closed', label: '已关闭' },
                    { value: 'sent', label: '已发送', disabled: true },
                  ]"
                  @change="(v) => changeStatus(selected, v)"
                />
                <a-tag v-if="selected.priority === 'urgent'" color="red">紧急</a-tag>
              </div>
              <div class="kv">
                <b>紧急程度</b>
                <a-select
                  :value="selected.priority || 'normal'"
                  size="small"
                  style="width: 92px"
                  :options="[
                    { value: 'urgent', label: '紧急' },
                    { value: 'normal', label: '普通' },
                    { value: 'low', label: '低' },
                  ]"
                  @change="(v) => changePriority(selected, v)"
                />
              </div>
              <div class="kv"><b>时间</b> {{ fmt(selected.created_at) }}</div>

              <div class="btn-row">
                <a-button size="small" @click="doSummarize">AI 摘要</a-button>
                <a-button size="small" @click="doDraft">AI 草稿</a-button>
                <a-button size="small" @click="makeTodo">转待办</a-button>
              </div>

              <div v-if="aiText" class="ai-box">{{ aiText }}</div>

              <a-textarea
                v-model:value="replyText"
                :rows="4"
                placeholder="输入回复内容，Ctrl + Enter 直接发送"
                style="margin-top: 10px"
              />
              <div class="btn-row">
                <a-button type="primary" size="small" :loading="sending" @click="sendReply">发送回复</a-button>
                <a-upload :before-upload="doSendFile" :show-upload-list="false">
                  <a-button size="small" :loading="sendingFile">📎 发文件</a-button>
                </a-upload>
                <a-button size="small" @click="setStatus('claimed')">标记处理中</a-button>
                <a-button size="small" @click="setStatus('closed')">关闭</a-button>
              </div>

              <a-checkbox v-model:checked="reflowOn" class="reflow-check">
                改状态时给对方发一条回执
                <span class="reflow-hint">（默认不发，避免刷屏）</span>
              </a-checkbox>

              <div class="divider">转发到其他会话</div>
              <div class="btn-row">
                <a-select
                  v-model:value="forwardKey"
                  size="small"
                  style="flex: 1"
                  placeholder="选择一个目标会话"
                  :options="forwardTargets"
                  not-found-content="还没有其他会话可转发"
                />
                <a-button size="small" :disabled="!forwardKey" @click="doForward">转发</a-button>
              </div>
            </div>
          </a-tab-pane>

          <a-tab-pane key="todos" tab="待办">
            <a-empty v-if="!todos.length" description="暂无待办" />
            <div v-for="t in todos" :key="t.id" class="todo-item">
              <div class="todo-title">{{ t.title }}</div>
              <div class="btn-row">
                <a-tag :color="PLATFORM_COLOR[t.platform]">{{ PLATFORM_LABEL[t.platform] }}</a-tag>
                <a-tag :color="TODO_STATUS_COLOR[t.status]">{{ TODO_STATUS_LABEL[t.status] || t.status }}</a-tag>
                <span class="meta">{{ fmt(t.created_at) }}</span>
                <a-button v-if="t.status !== 'done'" size="small" type="link" @click="completeTodo(t)">
                  完成并回流
                </a-button>
              </div>
            </div>
          </a-tab-pane>

          <a-tab-pane key="stats" tab="看板">
            <div v-if="stats" class="panel">
              <div class="kpi">
                <div class="kpi-num">{{ Math.round(stats.avg_handle_seconds || 0) }}<span class="unit">秒</span></div>
                <div class="kpi-label">平均处理时长</div>
              </div>
              <div class="divider">消息</div>
              <div class="kv">
                已回复 <b>{{ stats.by_status.replied || 0 }}</b> 条 ·
                已关闭 <b>{{ stats.by_status.closed || 0 }}</b> 条 ·
                待处理 <b>{{ (stats.by_status.received || 0) + (stats.by_status.claimed || 0) }}</b> 条
              </div>
              <div class="divider">出站队列</div>
              <div class="kv">
                已发送 <b>{{ stats.outbox.sent || 0 }}</b> ·
                排队中 <b>{{ (stats.outbox.queued || 0) + (stats.outbox.sending || 0) }}</b> ·
                失败 <b>{{ (stats.outbox.failed || 0) + (stats.outbox.dead || 0) }}</b>
              </div>
              <div class="divider">待办</div>
              <div class="kv">
                待处理 <b>{{ stats.todos.pending || 0 }}</b> · 进行中 <b>{{ stats.todos.in_progress || 0 }}</b> ·
                已完成 <b>{{ stats.todos.done || 0 }}</b>
              </div>
              <div class="divider">各平台</div>
              <div v-for="row in stats.by_platform" :key="row.platform" class="kv">
                {{ PLATFORM_LABEL[row.platform] || row.platform }}：收 {{ row.inbound }} / 回 {{ row.replied }} / 待
                {{ row.pending }}
              </div>
            </div>
            <a-empty v-else description="暂无统计" />
          </a-tab-pane>
        </a-tabs>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.app-root { height: 100vh; display: flex; flex-direction: column; }
.topbar {
  height: 56px; display: flex; align-items: center; justify-content: space-between;
  padding: 0 16px; background: #fff; border-bottom: 1px solid #f0f0f0;
}
.brand { font-weight: 700; font-size: 16px; }
.sub { font-weight: 400; font-size: 12px; color: #999; margin-left: 8px; }
.main { flex: 1; display: flex; min-height: 0; }
.col { display: flex; flex-direction: column; background: #fff; min-height: 0; }
.col-left { width: 300px; border-right: 1px solid #f0f0f0; }
.col-mid { flex: 1; border-right: 1px solid #f0f0f0; }
.col-right { width: 380px; }

/* 窄屏（手机 / 分屏）：三栏变单栏，点开会话后列表收起。
   这是"普通员工在手机上查一条消息"的最低要求，不做就只能在电脑上用。 */
.back-btn { display: none; }
@media (max-width: 900px) {
  .topbar { flex-wrap: wrap; gap: 6px; height: auto; padding: 8px 10px; }
  .main { flex-direction: column; overflow-y: auto; }
  .col { width: 100% !important; flex: none; border-right: none; }
  .col-left { display: flex; }
  .col-mid, .col-right { display: none; }
  .main.reading .col-left { display: none; }
  .main.reading .col-mid { display: flex; min-height: 70vh; }
  .main.reading .col-right { display: flex; min-height: 40vh; }
  .back-btn { display: inline-block; }
  .scroll { max-height: none; }
}
.flex-1 { flex: 1; min-height: 0; }
.msg-area { padding: 14px 16px; }
.mid-title { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.thread-top { display: flex; align-items: center; gap: 6px; }
.thread-title { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.thread-last { font-size: 12px; color: #666; margin: 4px 0 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bubble-head { display: flex; align-items: center; gap: 6px; font-size: 12px; margin-bottom: 4px; }
.bubble.picked { outline: 2px solid #91caff; }
.who { font-weight: 600; color: #333; }
.day-sep { text-align: center; margin: 14px 0 6px; color: #bbb; font-size: 12px; }
.day-sep span { background: #fafafa; padding: 2px 10px; border-radius: 10px; }
.panel { padding: 4px 2px; }
.kv { font-size: 13px; color: #333; margin-bottom: 6px; }
.btn-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.reflow-check { margin-top: 8px; font-size: 12px; color: #666; }
.reflow-hint { color: #bbb; }
.echo-toggle { font-size: 12px; color: #999; margin-left: auto; margin-right: 10px; }
.ai-box {
  margin-top: 10px; padding: 8px 10px; background: #f6ffed;
  border: 1px solid #b7eb8f; border-radius: 6px; font-size: 13px; white-space: pre-wrap;
}
.divider { margin: 14px 0 6px; font-size: 12px; color: #999; border-top: 1px dashed #f0f0f0; padding-top: 8px; }
.todo-item { padding: 8px 0; border-bottom: 1px solid #fafafa; }
.todo-title { font-size: 13px; margin-bottom: 4px; }
.tabs { padding: 0 12px; }
.kpi { text-align: center; padding: 10px 0; }
.kpi-num { font-size: 30px; font-weight: 700; color: #1677ff; }
.kpi-num .unit { font-size: 13px; font-weight: 400; color: #999; margin-left: 4px; }
.search-bar { padding: 8px 12px; border-bottom: 1px solid #f5f5f5; }
.search-hint { font-size: 11px; color: #bbb; }
.thread-last :deep(mark) { background: #ffe58f; color: inherit; padding: 0 1px; border-radius: 2px; }
.att-list { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.att-img-wrap { display: inline-block; line-height: 0; }
.att-img {
  max-width: 220px; max-height: 220px; border-radius: 6px;
  border: 1px solid #eee; object-fit: cover; cursor: zoom-in;
}
.att-file {
  display: inline-flex; align-items: center; gap: 6px; max-width: 100%;
  padding: 6px 10px; background: #fafafa; border: 1px solid #eee; border-radius: 6px;
  font-size: 12px; color: #333; text-decoration: none;
}
.att-file:hover { background: #f0f7ff; border-color: #bae0ff; }
.att-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.att-dl { color: #1677ff; font-size: 11px; }
.urgent-flag {
  flex: none; font-size: 11px; line-height: 16px; padding: 0 5px; border-radius: 3px;
  background: #fff1f0; color: #cf1322; border: 1px solid #ffccc7; font-weight: 600;
}
.pending-hint { color: #d46b08; }
.unread-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #1677ff;
  display: inline-block; flex: none;
}
.bulk-toolbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 7px 16px; background: #fafafa; border-bottom: 1px solid #f0f0f0; flex: none;
}
.bulk-count { font-size: 12px; color: #1677ff; font-weight: 600; }
.bulk-clear { font-size: 12px; color: #8c8c8c; cursor: pointer; margin-left: auto; }
.bulk-clear:hover { color: #1677ff; }
.bulk-check { flex: none; margin-right: 2px; }
/* 勾选中的气泡给个底色，不然小方框在长列表里看不清选了哪几条 */
.bubble:has(.ant-checkbox-wrapper-checked) {
  background: #f0f7ff;
  border-color: #91caff;
}
.acc-meta { font-size: 12px; color: #8c8c8c; line-height: 1.8; margin-bottom: 8px; }
.acc-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.chat-list { margin-top: 10px; max-height: 220px; overflow-y: auto; border-top: 1px solid #f0f0f0; }
.chat-row {
  display: flex; justify-content: space-between; gap: 8px;
  padding: 5px 0; font-size: 12px; border-bottom: 1px dashed #f5f5f5;
}
.chat-name { color: #262626; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chat-meta { color: #bfbfbf; flex: none; }
.kpi-label { font-size: 12px; color: #999; }
.guide-list {
  margin: 0 0 8px;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.9;
  color: #595959;
}
.guide-list li { margin-bottom: 2px; }
.guide-link { font-size: 12px; color: #1677ff; }
</style>
