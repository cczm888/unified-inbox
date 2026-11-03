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
// 库里保存的平台连接（运行时接入的那些，重启后自动恢复）
const savedPlats = ref([])
const threads = ref([])
const messages = ref([])
const todos = ref([])
const stats = ref(null)

// 平均处理时长：按量级自动换单位，避免出现「21034 秒」这种没法读的数
const handleNum = computed(() => {
  const s = Number(stats.value?.avg_handle_seconds || 0)
  if (!s) return '—'
  if (s < 60) return String(Math.round(s))
  if (s < 3600) return (s / 60).toFixed(s < 600 ? 1 : 0)
  if (s < 86400) return (s / 3600).toFixed(1)
  return (s / 86400).toFixed(1)
})
const handleUnit = computed(() => {
  const s = Number(stats.value?.avg_handle_seconds || 0)
  if (!s) return ''
  if (s < 60) return '秒'
  if (s < 3600) return '分'
  if (s < 86400) return '小时'
  return '天'
})
const todoPending = computed(() => {
  const t = stats.value?.todos || {}
  return Number(t.pending || 0) + Number(t.in_progress || 0)
})
const msgPending = computed(() => {
  const b = stats.value?.by_status || {}
  return Number(b.received || 0) + Number(b.claimed || 0)
})
const activePlatform = ref(undefined)
const activeThread = ref(null)
const selected = ref(null)
const replyText = ref('')
// 当前正在引用哪条消息（null = 不引用）
const quoting = ref(null)
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

// 列表里的时间只需要"够判断新旧"：今天给时刻，昨天给"昨天"，更早给日期
function shortTime(v) {
  if (!v) return ''
  const d = dayjs(v)
  const today = dayjs().startOf('day')
  if (d.isSame(today, 'day')) return d.format('HH:mm')
  if (d.isSame(today.subtract(1, 'day'), 'day')) return '昨天'
  if (d.isAfter(today.subtract(7, 'day'))) return d.format('ddd')
  return d.format('MM-DD')
}

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
  // 已接入的平台（存在库里的连接，重启后自动恢复）
  try {
    const r = await api.savedPlatforms()
    savedPlats.value = r.items || []
  } catch (e) {
    savedPlats.value = []
  }
}

async function removeSaved(s) {
  try {
    await api.disconnectPlatform(s.platform)
    antMessage.success(`已断开${PLATFORM_LABEL[s.platform] || s.platform}`)
    await loadAccounts()
    await loadPlatforms()
    await loadThreads()
  } catch (e) {
    antMessage.error('断开失败：' + e.message)
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
    const qid = quoting.value ? quoting.value.id : null
    const res = await api.reply(selected.value.id, replyText.value.trim(), qid)
    notify(
      () =>
        antMessage.success(
          qid
            ? `已带引用发送到${PLATFORM_LABEL[res.platform] || res.platform}`
            : `已发送到${PLATFORM_LABEL[res.platform] || res.platform}`,
        ),
      '已发送',
    )
    replyText.value = ''
    quoting.value = null
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

// 引用：点气泡上的「引用」把那条挂到输入框上方，发出去时后端拼成引用块。
// 不默认开着，是因为多数回复不需要引用——每次都要多按一个 × 反而烦。
function startQuote(m) {
  if (!m) return
  quoting.value = m
  nextTick(() => {
    const el = document.querySelector('.reply-box textarea')
    if (el) el.focus()
  })
}

function cancelQuote() {
  quoting.value = null
}

// 引用条上显示的一行摘要（和前端气泡里的显示一致）
function quoteBrief(m) {
  if (!m) return ''
  const t = (m.text || '').trim()
  if (t) return t.length > 60 ? t.slice(0, 60) + '…' : t
  const a = (m.attachments || [])[0]
  if (a) return (a.kind === 'image' ? '[图片] ' : '[文件] ') + (a.name || '附件')
  return '（无文字内容）'
}

// 把「引用块 + 正文」拆开显示：后端为了跨平台一致把引用压成了一行文本，
// 界面上不该让它和正文混在一起——左边一条竖线隔开，才看得出哪句是被引用的。
const _quoteCache = new Map()
function parseQuote(m) {
  if (_quoteCache.has(m.id)) return _quoteCache.get(m.id)
  const t = m.text || ''
  const hit = /^「引用([\s\S]*?)」\n\n?/.exec(t)
  const res = hit ? { q: hit[1].trim(), b: t.slice(hit[0].length) } : { q: '', b: t }
  _quoteCache.set(m.id, res)
  return res
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
        <div class="logo">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 12h-6l-2 3h-4l-2-3H2" />
            <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
          </svg>
        </div>
        <div class="brand-text">
          <div class="brand-title">统一消息收件箱</div>
          <div class="brand-sub">多平台连接器 · 赛题 A</div>
        </div>
      </div>

      <span class="vline sep"></span>

      <div class="pstatus">
        <span v-for="p in platforms" :key="p.platform" class="pitem" :class="'p-' + p.platform">
          <i class="pdot" :class="{ off: !p.ready }"></i>
          <span class="pname">{{ p.name }}</span>
          <span class="pstate">{{ p.ready ? '在线' : '未就绪' }}</span>
        </span>
        <span v-if="!platforms.length" class="pitem pnone">没有已接入的平台</span>
      </div>

      <div class="topbar-right">
        <div class="metrics">
          <span v-if="unreadTotal" class="metric m-blue">未读 <b>{{ unreadTotal }}</b></span>
          <span v-if="urgentTotal" class="metric m-red">紧急 <b>{{ urgentTotal }}</b></span>
          <span class="metric" :class="pendingTotal ? 'm-orange' : 'm-idle'">待处理 <b>{{ pendingTotal }}</b></span>
        </div>
        <span class="vline"></span>
        <button class="ghost-btn" @click="openAccounts">
          <span class="gb-ico">👤</span>账号
          <span v-if="userAccounts.length" class="gb-badge">{{ userAccounts.length }}</span>
        </button>
        <a-tooltip v-if="canNotify" :title="notifyOn ? '桌面通知已开，点击关闭' : '收新消息时弹系统通知'">
          <button class="ghost-btn icon-only" :class="{ on: notifyOn }" @click="toggleNotify">
            {{ notifyOn ? '🔔' : '🔕' }}
          </button>
        </a-tooltip>
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

      <a-divider>已接入的平台</a-divider>
      <div v-if="!savedPlats.length" class="acc-empty">
        还没有接入任何平台。下面填一次凭证就会出现在这里，重启后自动恢复。
      </div>
      <div v-for="s in savedPlats" :key="s.id" class="saved-card">
        <div class="saved-row">
          <span class="st-pill plat-pill">{{ PLATFORM_LABEL[s.platform] || s.platform }}</span>
          <span class="saved-label" :title="s.label || s.app_id">{{ s.label || s.app_id }}</span>
          <span class="saved-status" :class="s.status === 'active' ? 'is-ok' : 'is-bad'">
            {{ s.status === 'active' ? '已连接' : '异常' }}
          </span>
          <a-popconfirm title="断开这个平台？库里的凭证会一起删掉" @confirm="removeSaved(s)">
            <a-button size="small" type="text" danger>断开</a-button>
          </a-popconfirm>
        </div>
      </div>

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
        <div class="scroll flex-1 thread-scroll">
          <div v-if="!filteredThreads.length" class="empty-mini">
            <svg v-if="keyword" class="es-svg sm" viewBox="0 0 48 48" fill="none" aria-hidden="true">
              <circle cx="21" cy="21" r="12" stroke="currentColor" stroke-width="2.5" />
              <path d="M30 30l9 9" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
            </svg>
            <svg v-else class="es-svg sm" viewBox="0 0 48 48" fill="none" aria-hidden="true">
              <path d="M8 12h32v20H26l-8 7v-7H8z" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
            </svg>
            <div class="em-title">{{ keyword ? '没搜到' : '还没有收到消息' }}</div>
            <div class="em-desc">
              {{ keyword ? '换个词，或者清空搜索框' : '在「账号」里接入平台后，消息会自动出现在这里' }}
            </div>
            <button v-if="!keyword" class="link-btn" @click="openAccounts">去接入平台 →</button>
          </div>
          <div
            v-for="t in filteredThreads"
            :key="t.platform + t.chat_id"
            class="thread-item"
            :class="[
              'p-' + t.platform,
              {
                active:
                  activeThread && activeThread.chat_id === t.chat_id && activeThread.platform === t.platform,
              },
            ]"
            @click="openThread(t)"
          >
            <span class="t-bar"></span>
            <div class="t-main">
              <div class="t-row1">
                <span class="t-name">{{ t.chat_title || t.chat_id }}</span>
                <span class="t-time">{{ shortTime(t.last_at) }}</span>
              </div>
              <div class="t-row2" v-html="highlight(t.last_text || '（无文本内容）')"></div>
              <div class="t-row3">
                <span class="t-plat">{{ PLATFORM_LABEL[t.platform] || t.platform }}</span>
                <span v-if="Number(t.pending) > 0" class="t-pending">{{ t.pending }} 条待处理</span>
                <span class="t-spacer"></span>
                <span class="t-total">共 {{ t.total }} 条</span>
                <span v-if="Number(t.urgent) > 0" class="t-urgent">急</span>
                <span v-if="Number(t.unread) > 0" class="t-unread">{{ t.unread }}</span>
              </div>
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
          <div v-if="!activeThread" class="empty-state">
            <svg class="es-svg" viewBox="0 0 64 64" fill="none" aria-hidden="true">
              <rect x="7" y="15" width="50" height="34" rx="9" stroke="currentColor" stroke-width="2.5" />
              <path d="M7 26h15l4.5 6.5h11L42 26h15" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
            </svg>
            <div class="es-title">选一个会话开始</div>
            <div class="es-desc">左侧列出所有平台的会话，点开就是完整对话</div>
          </div>
          <div v-else-if="!visibleMessages.length" class="empty-state">
            <svg class="es-svg" viewBox="0 0 64 64" fill="none" aria-hidden="true">
              <path d="M12 16h40v26a4 4 0 0 1-4 4H30l-10 8v-8h-4a4 4 0 0 1-4-4V16z" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" />
              <path d="M22 27h20M22 34h12" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
            </svg>
            <div class="es-title">这个会话还没有消息</div>
            <div class="es-desc">等对方发一条，或者在右侧发一条过去</div>
          </div>
          <template v-for="row in timeline" :key="row.key">
            <div v-if="row.kind === 'day'" class="day-sep">
              <span>{{ row.label }}</span>
            </div>
            <div
              v-else
              class="msg-row"
              :class="['p-' + row.m.platform, { out: row.m.direction === 'out' }]"
            >
              <span v-if="row.m.direction === 'in'" class="avatar">{{ (row.m.sender_name || '?').slice(0, 1) }}</span>
              <div
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
                  <span
                    class="st-pill"
                    :class="'st-' + row.m.status"
                    title="点一下改状态"
                  >{{ STATUS_LABEL[row.m.status] || row.m.status }}</span>
                  <template #overlay>
                    <a-menu @click="({ key }) => changeStatus(row.m, key)">
                      <a-menu-item key="received">待处理</a-menu-item>
                      <a-menu-item key="claimed">处理中</a-menu-item>
                      <a-menu-item key="replied">已回复</a-menu-item>
                      <a-menu-item key="closed">已关闭</a-menu-item>
                    </a-menu>
                  </template>
                </a-dropdown>
                <span
                  v-if="row.m.priority === 'urgent'"
                  class="urgent-pill"
                  title="点 × 取消紧急标记"
                >紧急<i class="up-x" @click.stop="clearPriority(row.m)">×</i></span>
                <button class="quote-btn" title="引用这条再回复" @click.stop="startQuote(row.m)">引用</button>
              </div>
              <div v-if="parseQuote(row.m).q" class="q-quote">{{ parseQuote(row.m).q }}</div>
              <div v-if="parseQuote(row.m).b" class="msg-text">{{ parseQuote(row.m).b }}</div>
              <div v-else-if="!row.m.attachments || !row.m.attachments.length" class="msg-text faint">
                （非文本消息）
              </div>
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
              <div class="bubble-foot"><span class="meta">{{ fmt(row.m.created_at) }}</span></div>
            </div>
            </div>
          </template>
        </div>
      </section>

      <!-- 右：处理面板 -->
      <aside class="col col-right">
        <a-tabs size="small" class="flex-1 tabs">
          <a-tab-pane key="handle" tab="处理">
            <div v-if="!selected" class="empty-state tight">
              <svg class="es-svg sm" viewBox="0 0 48 48" fill="none" aria-hidden="true">
                <rect x="7" y="11" width="34" height="26" rx="6" stroke="currentColor" stroke-width="2.5" />
                <path d="M16 22h16M16 29h10" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
              </svg>
              <div class="es-title">点一条消息</div>
              <div class="es-desc">选中后这里会出现状态、回复、转发</div>
            </div>
            <div v-else class="panel">
              <div class="info-card" :class="'p-' + selected.platform">
                <div class="ic-top">
                  <span class="ic-avatar">{{ (selected.direction === 'out' ? '我' : (selected.sender_name || '?')).slice(0, 1) }}</span>
                  <div class="ic-id">
                    <div class="ic-name">
                      {{ selected.direction === 'out' ? '我（本站发出）' : selected.sender_name || '未知用户' }}
                    </div>
                    <div class="ic-sub">{{ activeThread ? activeThread.title : selected.chat_id }}</div>
                  </div>
                  <span class="ic-plat">{{ PLATFORM_LABEL[selected.platform] }}</span>
                </div>
                <div class="ic-row">
                  <span class="ic-label">状态</span>
                  <a-select
                    :value="selected.status"
                    size="small"
                    style="width: 96px"
                    :options="[
                      { value: 'received', label: '待处理' },
                      { value: 'claimed', label: '处理中' },
                      { value: 'replied', label: '已回复' },
                      { value: 'closed', label: '已关闭' },
                      { value: 'sent', label: '已发送', disabled: true },
                    ]"
                    @change="(v) => changeStatus(selected, v)"
                  />
                  <span class="ic-label" style="margin-left: 12px">紧急度</span>
                  <a-select
                    :value="selected.priority || 'normal'"
                    size="small"
                    style="width: 84px"
                    :options="[
                      { value: 'urgent', label: '紧急' },
                      { value: 'normal', label: '普通' },
                      { value: 'low', label: '低' },
                    ]"
                    @change="(v) => changePriority(selected, v)"
                  />
                </div>
                <div class="ic-row">
                  <span class="ic-label">时间</span>
                  <span class="ic-val">{{ fmt(selected.created_at) }}</span>
                </div>
              </div>

              <div class="sec">
                <div class="sec-head">
                  <span class="sec-title">AI 辅助</span>
                </div>
                <div class="btn-row">
                  <a-button size="small" @click="doSummarize">摘要</a-button>
                  <a-button size="small" @click="doDraft">起草回复</a-button>
                  <a-button size="small" @click="makeTodo">转待办</a-button>
                </div>
                <div v-if="aiText" class="ai-box">{{ aiText }}</div>
              </div>

              <div class="sec">
                <div class="sec-head">
                  <span class="sec-title">回复</span>
                  <span class="sec-hint">Ctrl + Enter 直接发</span>
                </div>
                <div v-if="quoting" class="quote-bar">
                  <span class="quote-mark">引用</span>
                  <span class="quote-who">{{ quoting.direction === 'out' ? '我' : quoting.sender_name || '对方' }}</span>
                  <span class="quote-txt">{{ quoteBrief(quoting) }}</span>
                  <button class="quote-close" title="取消引用" @click="cancelQuote">×</button>
                </div>
                <a-textarea
                  v-model:value="replyText"
                  :rows="4"
                  class="reply-box"
                  :placeholder="quoting ? '针对上面这条回复…' : '打字回复，会直接发到对方所用平台…'"
                />
                <div class="btn-row">
                  <a-button type="primary" :loading="sending" @click="sendReply">发送</a-button>
                  <a-upload :before-upload="doSendFile" :show-upload-list="false">
                    <a-button :loading="sendingFile">📎 发文件</a-button>
                  </a-upload>
                  <span class="btn-gap"></span>
                  <a-button size="small" @click="setStatus('claimed')">处理中</a-button>
                  <a-button size="small" @click="setStatus('closed')">关闭</a-button>
                </div>
                <a-checkbox v-model:checked="reflowOn" class="reflow-check">
                  改状态时给对方发一条回执
                  <span class="reflow-hint">默认不发，避免刷屏</span>
                </a-checkbox>
              </div>

              <div class="sec">
                <div class="sec-head">
                  <span class="sec-title">转发到其他会话</span>
                </div>
                <div class="btn-row">
                  <a-select
                    v-model:value="forwardKey"
                    style="flex: 1"
                    placeholder="选一个目标会话"
                    :options="forwardTargets"
                    not-found-content="还没有其他会话可转发"
                  />
                  <a-button :disabled="!forwardKey" @click="doForward">转发</a-button>
                </div>
              </div>
            </div>
          </a-tab-pane>

          <a-tab-pane key="todos" tab="待办">
            <div v-if="!todos.length" class="empty-state tight">
              <svg class="es-svg sm" viewBox="0 0 48 48" fill="none" aria-hidden="true">
                <rect x="10" y="9" width="28" height="32" rx="6" stroke="currentColor" stroke-width="2.5" />
                <path d="M17 19h14M17 26h14M17 33h8" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
              </svg>
              <div class="es-title">还没有待办</div>
              <div class="es-desc">在消息上点「转待办」，它就会出现在这里</div>
            </div>
            <div
              v-for="t in todos"
              :key="t.id"
              class="todo-item"
              :class="{ 'is-done': t.status === 'done' }"
            >
              <div class="td-top">
                <span class="td-dot" :class="'td-' + t.status"></span>
                <span class="td-title">{{ t.title }}</span>
              </div>
              <div class="td-meta">
                <span class="st-pill plat-pill">{{ PLATFORM_LABEL[t.platform] || t.platform }}</span>
                <span class="st-pill" :class="'todo-' + t.status">{{ TODO_STATUS_LABEL[t.status] || t.status }}</span>
                <span class="meta">{{ fmt(t.created_at) }}</span>
                <a-button
                  v-if="t.status !== 'done'"
                  size="small"
                  type="link"
                  class="td-act"
                  @click="completeTodo(t)"
                >完成</a-button>
              </div>
            </div>
          </a-tab-pane>

          <a-tab-pane key="stats" tab="看板">
            <div v-if="stats" class="panel">
              <div class="kpi-card">
                <div class="kpi-num">{{ handleNum }}<span class="kpi-unit">{{ handleUnit }}</span></div>
                <div class="kpi-label">平均处理时长</div>
              </div>

              <div class="stat-grid">
                <div class="stat-cell">
                  <div class="sc-num">{{ stats.by_status.replied || 0 }}</div>
                  <div class="sc-label">已回复</div>
                </div>
                <div class="stat-cell">
                  <div class="sc-num">{{ stats.by_status.closed || 0 }}</div>
                  <div class="sc-label">已关闭</div>
                </div>
                <div class="stat-cell">
                  <div class="sc-num warn">{{ msgPending }}</div>
                  <div class="sc-label">待处理</div>
                </div>
              </div>

              <div class="sec">
                <div class="sec-title">出站队列</div>
                <div class="bar-row">
                  <span class="br-label">已发送</span>
                  <span class="br-num">{{ stats.outbox.sent || 0 }}</span>
                </div>
                <div class="bar-row">
                  <span class="br-label">排队 / 发送中</span>
                  <span class="br-num">{{ (stats.outbox.queued || 0) + (stats.outbox.sending || 0) }}</span>
                </div>
                <div class="bar-row">
                  <span class="br-label">失败</span>
                  <span class="br-num" :class="{ bad: (stats.outbox.failed || 0) + (stats.outbox.dead || 0) > 0 }">
                    {{ (stats.outbox.failed || 0) + (stats.outbox.dead || 0) }}
                  </span>
                </div>
              </div>

              <div class="sec">
                <div class="sec-title">待办</div>
                <div class="bar-row">
                  <span class="br-label">未完成</span>
                  <span class="br-num">{{ todoPending }}</span>
                </div>
                <div class="bar-row">
                  <span class="br-label">已完成</span>
                  <span class="br-num">{{ stats.todos.done || 0 }}</span>
                </div>
              </div>

              <div class="sec">
                <div class="sec-title">各平台</div>
                <div v-for="row in stats.by_platform" :key="row.platform" class="plat-row">
                  <span class="st-pill plat-pill">{{ PLATFORM_LABEL[row.platform] || row.platform }}</span>
                  <span class="pr-nums">收 {{ row.inbound }} · 回 {{ row.replied }} · 待 {{ row.pending }}</span>
                </div>
                <div v-if="!stats.by_platform || !stats.by_platform.length" class="kv">还没有平台数据</div>
              </div>
            </div>
            <div v-else class="empty-state tight">
              <svg class="es-svg sm" viewBox="0 0 48 48" fill="none" aria-hidden="true">
                <path d="M8 36V22M19 36V14M30 36V26M41 36V19" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
              </svg>
              <div class="es-title">还没有数据</div>
              <div class="es-desc">收发几条消息后，这里会出现统计</div>
            </div>
          </a-tab-pane>
        </a-tabs>
      </aside>
    </div>
  </div>
</template>

<style scoped>
/* ================= 骨架 ================= */
.app-root { height: 100vh; display: flex; flex-direction: column; background: var(--bg); color: var(--t1); }

/* ================= 顶栏 ================= */
.topbar {
  flex: none; height: 60px; display: flex; align-items: center; gap: 22px;
  padding: 0 20px; background: var(--panel); border-bottom: 1px solid var(--line);
  position: relative; z-index: 5;
}
.brand { display: flex; align-items: center; gap: 10px; flex: none; }
.logo {
  width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center;
  color: #fff; background: linear-gradient(135deg, #5a8dff, var(--brand) 55%, #1f4fd8);
  box-shadow: 0 2px 7px rgba(47, 107, 255, 0.35);
}
.brand-title { font-size: 14px; font-weight: 650; line-height: 1.25; letter-spacing: 0.2px; }
.brand-sub { font-size: 11px; color: var(--t3); margin-top: 1px; }

.pstatus { display: flex; align-items: center; gap: 16px; flex: none; min-width: 0; }
.pitem { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--t2); white-space: nowrap; }
.pdot { width: 7px; height: 7px; border-radius: 50%; background: var(--p, var(--green)); flex: none; }
.pdot.off { background: #cbd2dc; }
.pstate { color: var(--t3); font-size: 11px; }
.pnone { color: var(--t3); font-size: 12px; }

.topbar-right { display: flex; align-items: center; gap: 10px; flex: none; margin-left: auto; }
.metrics { display: flex; align-items: center; gap: 6px; }
.metric {
  font-size: 12px; color: var(--t2); padding: 3px 10px; border-radius: 20px;
  background: var(--sub); border: 1px solid var(--line-soft); white-space: nowrap;
}
.metric b { font-weight: 680; }
.m-blue { color: var(--brand); background: var(--brand-weak); border-color: var(--brand-line); }
.m-red { color: var(--red); background: var(--red-weak); border-color: var(--red-line); }
.m-orange { color: var(--orange); background: var(--orange-weak); border-color: var(--orange-line); }
.m-idle { color: var(--t3); }
.vline { width: 1px; height: 20px; background: var(--line); flex: none; }
.vline.sep { margin: 0 2px; }
.ghost-btn {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px;
  border-radius: 8px; border: 1px solid transparent; background: transparent;
  color: var(--t2); font-size: 12.5px; cursor: pointer; font-family: inherit;
  transition: background 0.12s, color 0.12s;
}
.ghost-btn:hover { background: var(--hover); color: var(--t1); }
.ghost-btn.on { color: var(--brand); background: var(--brand-weak); }
.icon-only { font-size: 14px; padding: 0 9px; }
.gb-ico { font-size: 13px; }
.gb-badge {
  background: var(--brand); color: #fff; border-radius: 9px; font-size: 10px;
  padding: 0 5px; line-height: 15px; font-weight: 600;
}

/* ================= 三栏 ================= */
.main { flex: 1; display: flex; min-height: 0; }
.col { display: flex; flex-direction: column; min-height: 0; background: var(--panel); }
.col-left { width: 308px; flex: none; background: var(--sub); border-right: 1px solid var(--line); }
.col-mid { flex: 1; min-width: 0; }
.col-right { width: 372px; flex: none; border-left: 1px solid var(--line); }

/* 窄屏（手机 / 分屏）：三栏变单栏，点开会话后列表收起。
   这是"普通员工在手机上查一条消息"的最低要求，不做就只能在电脑上用。 */
.back-btn { display: none; }
@media (max-width: 900px) {
  .topbar { flex-wrap: wrap; gap: 8px; height: auto; padding: 10px 12px; }
  .pstatus { gap: 10px; }
  .main { flex-direction: column; overflow-y: auto; }
  .col { width: 100% !important; flex: none; border-right: none; border-left: none; }
  .col-left { display: flex; }
  .col-mid, .col-right { display: none; }
  .main.reading .col-left { display: none; }
  .main.reading .col-mid { display: flex; min-height: 70vh; }
  .main.reading .col-right { display: flex; min-height: 40vh; }
  .back-btn { display: inline-block; }
  .scroll { max-height: none; }
  .bubble { max-width: 74%; }
  .msg-area { padding: 14px 14px 20px; }
}

.flex-1 { flex: 1; min-height: 0; }
.msg-area { padding: 20px 26px 26px; background: #fbfcfd; }
.mid-title { display: flex; align-items: center; gap: 8px; font-weight: 650; font-size: 13.5px; flex: 1; min-width: 0; }

/* ================= 左侧列表 ================= */
.col-left .col-head { background: transparent; border-bottom: 1px solid var(--line); }
.col-left .col-head > span:first-child { font-weight: 650; font-size: 13.5px; }
.search-bar { padding: 10px 12px 8px; }
.thread-scroll { padding: 4px 8px 14px; }

.t-bar {
  position: absolute; left: 5px; top: 13px; bottom: 13px; width: 3px;
  border-radius: 2px; background: var(--p, var(--t3)); opacity: 0; transition: opacity 0.12s;
}
.thread-item.active .t-bar { opacity: 1; }
.t-main { flex: 1; min-width: 0; }
.t-row1 { display: flex; align-items: baseline; gap: 8px; }
.t-name {
  flex: 1; font-size: 13px; font-weight: 600; color: var(--t1);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.t-time { flex: none; font-size: 11px; color: var(--t3); }
.t-row2 {
  font-size: 12px; color: var(--t2); margin-top: 3px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.t-row3 { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 11px; min-height: 16px; }
.t-plat { color: var(--p, var(--t3)); font-weight: 600; }
.t-pending { color: var(--orange); }
.t-total { color: #c3c9d4; }
.t-spacer { flex: 1; }
.t-urgent {
  color: var(--red); background: var(--red-weak); border: 1px solid var(--red-line);
  border-radius: 4px; padding: 0 4px; font-weight: 700; line-height: 14px; font-size: 10.5px;
}
.t-unread {
  min-width: 16px; height: 16px; border-radius: 8px; background: var(--brand); color: #fff;
  font-size: 10.5px; display: grid; place-items: center; padding: 0 4px; font-weight: 600;
}
.thread-last :deep(mark) { background: #ffe58f; color: inherit; padding: 0 1px; border-radius: 2px; }
.t-row2 :deep(mark) { background: #ffe58f; color: inherit; padding: 0 1px; border-radius: 2px; }

/* 空状态 */
.empty-mini { padding: 56px 24px; text-align: center; }
.em-title { margin-top: 12px; font-size: 13px; color: var(--t2); font-weight: 650; }
.em-desc { margin-top: 5px; font-size: 12px; color: var(--t3); line-height: 1.6; }
.link-btn {
  margin-top: 14px; border: none; background: none; color: var(--brand);
  font-size: 12px; cursor: pointer; font-family: inherit;
}
.link-btn:hover { text-decoration: underline; }
.empty-state {
  height: 100%; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 3px; padding: 40px;
}
.empty-state.tight { height: auto; padding: 48px 16px; }
.es-svg { width: 64px; height: 64px; color: #d3dae4; margin-bottom: 16px; flex: none; }
.es-svg.sm { width: 46px; height: 46px; margin-bottom: 12px; }
.es-title { font-size: 14px; font-weight: 650; color: var(--t2); }
.es-desc { font-size: 12.5px; color: var(--t3); text-align: center; line-height: 1.6; }

/* ================= 消息流 ================= */
.day-sep { text-align: center; margin: 18px 0 12px; }
.day-sep span { font-size: 11.5px; color: var(--t3); background: var(--sub); padding: 3px 12px; border-radius: 20px; }
.msg-row { display: flex; align-items: flex-start; gap: 9px; margin-bottom: 14px; }
.bubble { margin-bottom: 0; }
.bubble-foot { text-align: right; margin-top: 6px; line-height: 1; }
.bubble-foot .meta { font-size: 11px; color: var(--t3); }

/* ================= 待办卡片 ================= */
.todo-item {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r);
  padding: 11px 12px; margin-bottom: 9px; transition: border-color .15s, box-shadow .15s;
}
.todo-item:hover { border-color: var(--brand-line); box-shadow: var(--sh1); }
.todo-item.is-done { opacity: .62; }
.td-top { display: flex; align-items: flex-start; gap: 7px; margin-bottom: 7px; }
.td-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; margin-top: 6px; background: var(--orange); }
.td-dot.td-in_progress { background: var(--brand); }
.td-dot.td-done { background: var(--green); }
.td-dot.td-cancelled { background: var(--t3); }
.td-title { font-size: 13px; line-height: 1.5; color: var(--t1); word-break: break-word; }
.td-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.td-act { margin-left: auto; padding: 0; height: 20px; font-size: 12px; }
.st-pill.todo-pending { background: #fff7e8; color: #ad6800; border-color: #ffe1b0; }
.st-pill.todo-in_progress { background: var(--brand-weak); color: var(--brand); border-color: var(--brand-line); }
.st-pill.todo-done { background: #eaf7f1; color: var(--green); border-color: #b7e3d0; }
.st-pill.todo-cancelled { background: #f3f4f6; color: var(--t3); border-color: var(--line); }

/* ================= 看板 ================= */
.kpi-card {
  background: linear-gradient(135deg, #f3f7ff, #eef3ff);
  border: 1px solid var(--brand-line); border-radius: var(--r);
  padding: 16px; text-align: center; margin-bottom: 12px;
}
.kpi-num { font-size: 30px; font-weight: 700; color: var(--brand); line-height: 1.1; letter-spacing: -.5px; }
.kpi-unit { font-size: 13px; font-weight: 500; margin-left: 3px; }
.kpi-label { font-size: 12px; color: var(--t2); margin-top: 5px; }
.stat-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 4px; }
.stat-cell {
  background: var(--sub); border: 1px solid var(--line-soft); border-radius: 9px;
  padding: 10px 6px; text-align: center;
}
.sc-num { font-size: 19px; font-weight: 600; color: var(--t1); line-height: 1.2; }
.sc-num.warn { color: var(--orange); }
.sc-label { font-size: 11px; color: var(--t3); margin-top: 3px; }
.bar-row { display: flex; align-items: center; justify-content: space-between; padding: 5px 0; font-size: 12.5px; }
.br-label { color: var(--t2); }
.br-num { font-weight: 600; color: var(--t1); }
.br-num.bad { color: var(--red); }
.plat-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; }
.pr-nums { font-size: 12.5px; color: var(--t2); }
.panel .sec { margin-top: 16px; }
.panel .sec .sec-title { margin-bottom: 4px; }
.bubble:has(.att-list) { max-width: min(100%, 340px); }
.msg-row.out { justify-content: flex-end; }
.avatar {
  width: 30px; height: 30px; border-radius: 50%; flex: none; margin-top: 2px;
  display: grid; place-items: center; font-size: 12px; font-weight: 600;
  color: #fff; background: var(--p, var(--brand)); box-shadow: var(--sh1);
}
.bubble-head { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }
.bubble.picked { border-color: #9dbcff; box-shadow: 0 0 0 3px rgba(47, 107, 255, 0.1), 0 2px 8px rgba(47, 107, 255, 0.08); }
.bubble:has(.ant-checkbox-wrapper-checked) { background: #f2f7ff; border-color: #9dbcff; }
.who { font-size: 12px; font-weight: 650; color: var(--t2); }
.bubble.out .who { color: var(--brand); }
.bubble-time { margin-left: auto; font-size: 11px; color: var(--t3); }

.st-pill {
  font-size: 11px; padding: 1px 8px; border-radius: 20px; cursor: pointer;
  user-select: none; border: 1px solid transparent; font-weight: 500; white-space: nowrap;
}
.st-pill:hover { filter: brightness(0.97); }
.st-received { color: var(--t2); background: var(--sub); border-color: var(--line); }
.st-claimed { color: var(--orange); background: var(--orange-weak); border-color: var(--orange-line); }
.st-replied { color: var(--green); background: var(--green-weak); border-color: var(--green-line); }
.st-closed { color: var(--t3); background: var(--sub); border-color: var(--line-soft); }
.st-sent { color: var(--brand); background: var(--brand-weak); border-color: var(--brand-line); }
.plat-pill { color: var(--t2); background: var(--sub); border-color: var(--line); cursor: default; }

.urgent-pill {
  display: inline-flex; align-items: center; gap: 2px; font-size: 11px; font-weight: 600;
  color: var(--red); background: var(--red-weak); border: 1px solid var(--red-line);
  border-radius: 20px; padding: 1px 3px 1px 8px;
}
.up-x { cursor: pointer; font-style: normal; padding: 0 4px; border-radius: 50%; line-height: 1; opacity: 0.65; }
.up-x:hover { opacity: 1; background: var(--red-line); }

.unread-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); flex: none; }
.bulk-check { flex: none; margin-right: 1px; opacity: 0; transition: opacity 0.12s; }
.bubble:hover .bulk-check,
.bubble:has(.ant-checkbox-wrapper-checked) .bulk-check { opacity: 1; }

.bulk-toolbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: none;
  padding: 8px 24px; background: var(--brand-weak); border-bottom: 1px solid var(--brand-line);
}
.bulk-count { font-size: 12px; color: var(--brand); font-weight: 650; }
.bulk-clear { font-size: 12px; color: var(--t2); cursor: pointer; margin-left: auto; }
.bulk-clear:hover { color: var(--brand); }

/* 附件 */
.q-quote {
  border-left: 3px solid var(--brand-line);
  background: rgba(47, 107, 255, 0.045);
  color: var(--t2);
  font-size: 12.5px;
  line-height: 1.5;
  padding: 5px 9px;
  border-radius: 0 6px 6px 0;
  margin-bottom: 7px;
  word-break: break-word;
}
.quote-btn {
  margin-left: auto;
  border: 0;
  background: transparent;
  color: var(--t3);
  font-size: 12px;
  cursor: pointer;
  padding: 0 2px;
  opacity: 0;
  transition: opacity 0.12s ease, color 0.12s ease;
}
.bubble:hover .quote-btn { opacity: 1; }
.quote-btn:hover { color: var(--brand); }
.acc-empty { font-size: 12px; color: var(--t3); line-height: 1.7; padding: 2px 0 10px; }
.saved-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  background: var(--panel);
}
.saved-row { display: flex; align-items: center; gap: 8px; }
.saved-label {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  color: var(--t1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.saved-status { font-size: 12px; flex: none; }
.saved-status.is-ok { color: var(--green); }
.saved-status.is-bad { color: var(--red); }
.quote-bar {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 8px;
  padding: 7px 9px;
  background: var(--sub);
  border-left: 2px solid var(--brand-line);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
}
.quote-mark { color: var(--brand); flex: none; }
.quote-who { color: var(--t2); flex: none; }
.quote-txt {
  color: var(--t3);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.quote-close {
  border: 0;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
  flex: none;
}
.quote-close:hover { color: var(--red); }
.msg-text.faint { color: var(--t3); font-size: 12px; }
.att-list { margin-top: 9px; display: flex; flex-direction: column; gap: 8px; }
.att-img-wrap { display: inline-block; line-height: 0; }
.att-img {
  max-width: 240px; max-height: 240px; border-radius: 10px;
  border: 1px solid var(--line-soft); object-fit: cover; cursor: zoom-in;
  transition: box-shadow 0.15s;
}
.att-img:hover { box-shadow: var(--sh2); }
.att-file {
  display: inline-flex; align-items: center; gap: 8px; max-width: 100%;
  padding: 8px 12px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; font-size: 12.5px; color: var(--t1);
  text-decoration: none; box-shadow: var(--sh1);
}
.att-file:hover { border-color: var(--brand-line); background: #f7faff; }
.att-icon { font-size: 13px; }
.att-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.att-dl { color: var(--brand); font-size: 11.5px; }

/* ================= 右栏 ================= */
.tabs { padding: 0 16px; }
.tabs :deep(.ant-tabs-nav) { margin-bottom: 10px; }
.tabs :deep(.ant-tabs-tab) { font-size: 13px; }
.panel { padding: 2px 0 24px; }

.info-card {
  border: 1px solid var(--line); border-radius: var(--r); padding: 13px 14px;
  background: var(--sub); margin-bottom: 4px;
}
.ic-top { display: flex; align-items: center; gap: 10px; margin-bottom: 11px; }
.ic-avatar {
  width: 32px; height: 32px; border-radius: 10px; display: grid; place-items: center;
  font-size: 13px; font-weight: 700; color: #fff; background: var(--p, var(--brand)); flex: none;
}
.ic-id { flex: 1; min-width: 0; }
.ic-name { font-size: 13px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ic-sub { font-size: 11.5px; color: var(--t3); margin-top: 1px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ic-plat {
  flex: none; font-size: 11px; color: var(--p, var(--t3)); background: var(--panel);
  border: 1px solid var(--line-soft); border-radius: 20px; padding: 1px 8px; font-weight: 500;
}
.ic-row { display: flex; align-items: center; gap: 8px; margin-top: 9px; }
.ic-label { font-size: 11.5px; color: var(--t3); white-space: nowrap; }
.ic-val { font-size: 12px; color: var(--t2); }

.sec { padding: 14px 0; border-top: 1px solid var(--line-soft); }
.sec-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.sec-title { font-size: 12px; font-weight: 650; color: var(--t2); letter-spacing: 0.3px; }
.sec-hint { font-size: 11px; color: var(--t3); }
.btn-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 9px; }
.btn-gap { flex: 1; }
.reply-box { border-radius: var(--r-sm); }
.reflow-check { margin-top: 10px; font-size: 11.5px; color: var(--t3); }
.reflow-hint { color: #c3c9d4; margin-left: 3px; }
.ai-box {
  margin-top: 10px; padding: 10px 12px; background: var(--green-weak);
  border: 1px solid var(--green-line); border-radius: var(--r-sm);
  font-size: 12.5px; white-space: pre-wrap; line-height: 1.65; color: #17604a;
}

.todo-item { padding: 11px 0; border-bottom: 1px solid var(--line-soft); }
.todo-title { font-size: 13px; margin-bottom: 6px; color: var(--t1); }
.kpi { text-align: center; padding: 18px 0 8px; }
.kpi-num { font-size: 34px; font-weight: 700; color: var(--brand); line-height: 1.1; letter-spacing: -0.5px; }
.kpi-num .unit { font-size: 13px; font-weight: 400; color: var(--t3); margin-left: 4px; }
.kpi-label { font-size: 12px; color: var(--t3); margin-top: 2px; }
.divider {
  margin: 16px 0 8px; padding-top: 10px; font-size: 11.5px; color: var(--t3);
  border-top: 1px solid var(--line-soft); font-weight: 650;
}
.kv { font-size: 12.5px; color: var(--t2); margin-bottom: 5px; }

/* ================= 抽屉（账号 / 接入） ================= */
.acc-meta { font-size: 12px; color: var(--t3); line-height: 1.8; margin-bottom: 8px; }
.acc-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.chat-list { margin-top: 10px; max-height: 220px; overflow-y: auto; border-top: 1px solid var(--line-soft); }
.chat-row {
  display: flex; justify-content: space-between; gap: 8px;
  padding: 6px 0; font-size: 12px; border-bottom: 1px dashed var(--line-soft);
}
.chat-name { color: var(--t1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chat-meta { color: var(--t3); flex: none; }
.guide-list { margin: 0 0 8px; padding-left: 18px; font-size: 12px; line-height: 1.9; color: var(--t2); }
.guide-list li { margin-bottom: 2px; }
.guide-link { font-size: 12px; color: var(--brand); }
</style>
