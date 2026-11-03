/**
 * 飞书网页版 DOM 采集器（外部进程）
 *
 * 定位：补充通道，不是主链路，不计入赛题「官方 API / Webhook 接入」的平台数量。
 * 官方 API 只覆盖「机器人参与的会话」；网页版 DOM 覆盖「这个账号登录后能看到的会话」，
 * 但爬页面不稳（前端改版、虚拟滚动、需要登录态），所以它不在后端进程里跑，
 * 而是作为独立采集器把结构化结果 POST /api/v1/ingest 推给后端。
 *
 * 用法：
 *   ego-browser nodejs -e "await import('file:///C:/Users/75090/Desktop/DEEP%20SEEK/unified-inbox/scripts/collect-feishu-web.mjs')"
 *
 * 环境变量：
 *   UTI_API       后端地址，默认 http://127.0.0.1:8000
 *   UTI_PASSWORD  访问口令
 *   UTI_PAGE      ego 页标签，默认 p2（飞书网页版所在页）
 */
const API = process.env.UTI_API || "http://127.0.0.1:8000";
const PASSWORD = process.env.UTI_PASSWORD || "94o32jglny1a";
const PAGE = process.env.UTI_PAGE || "p2";
const PLATFORM = "feishu_web";

const NOISE = new Set(["消息", "豆包工作", "会议", "日历", "云文档", "通讯录", "邮箱", "任务", "工作台", "搜索"]);

function hash(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

async function login() {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    body: new URLSearchParams({ password: PASSWORD }),
    redirect: "manual",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  const raw = res.headers.get("set-cookie") || "";
  const cookie = raw.split(";")[0];
  if (!cookie.startsWith("uti_auth=")) {
    throw new Error(`登录失败：status=${res.status} set-cookie=${raw.slice(0, 80)}`);
  }
  return cookie;
}

const task = await taskSpace("oauth-setup");
const page = task.page(PAGE);
await page.goto("https://www.feishu.cn/messenger/", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(9000);

// 1. 抓会话列表 —— 用几何特征定位（左栏固定 x≈156、宽≈448、每项高 55~75）
const threads = await page.evaluate((noiseList) => {
  const noise = new Set(noiseList);
  const out = [];
  const seen = new Set();
  for (const n of document.querySelectorAll("div")) {
    const r = n.getBoundingClientRect();
    if (r.left < 148 || r.left > 175) continue;
    if (r.width < 400 || r.width > 500) continue;
    if (r.height < 52 || r.height > 78) continue;
    if (r.top < 90) continue;
    const t = (n.innerText || "").trim();
    if (!t || t.length > 200) continue;
    const lines = t.split("\n").map((s) => s.trim()).filter(Boolean);
    const first = lines[0];
    if (!first || noise.has(first)) continue;
    if (/^\d{1,2}月\d{1,2}日$/.test(first)) continue;
    if (seen.has(first)) continue;
    seen.add(first);
    out.push({ name: first, lines: lines.slice(1), cy: Math.round(r.top + r.height / 2), cx: Math.round(r.left + r.width / 2) });
  }
  return out;
}, [...NOISE]);

console.log(`候选会话：${threads.length}`);
threads.forEach((t) => console.log(`  - ${t.name}  [${t.lines.join(" / ").slice(0, 50)}]`));

// 2. 逐个打开并采集
const items = [];
let prevHash = "";
for (const t of threads) {
  let body = "";
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await page.click(`loc=text=${t.name}`);
    } catch (e) {
      await page.evaluate(`(() => { const el = document.elementFromPoint(${t.cx}, ${t.cy}); if (el) el.click(); })()`);
    }
    await page.waitForTimeout(2800);
    body = await page.evaluate(() => {
      const root = document.querySelector(".page-view-messenger-chat");
      return root ? (root.innerText || "").trim() : "";
    });
    const h = hash(body);
    if (h !== prevHash && body) break; // 内容变了 = 切换成功
    await page.waitForTimeout(1200);
  }
  if (!body) {
    console.log(`  ${t.name}: 消息区为空，跳过`);
    continue;
  }
  const h = hash(body);
  if (h === prevHash) {
    console.log(`  ${t.name}: 内容与上一会话相同，判定未切换，跳过`);
    continue;
  }
  prevHash = h;

  // 去掉顶部（会话名 / 简介 / 机器人标签），从第一个日期行开始
  const lines = body.split("\n").map((s) => s.trim());
  const cut = lines.findIndex((l) => /^\d{1,2}月\d{1,2}日$|^(今天|昨天)$/.test(l));
  const text = (cut > 0 ? lines.slice(cut) : lines).join("\n").slice(0, 6000);
  items.push({
    platform_msg_id: `web:${t.name}:${h}`,
    chat_id: `web:${t.name}`,
    chat_name: t.name,
    sender_name: t.name,
    text,
    direction: "in",
  });
  console.log(`  ${t.name}: 采集 ${text.length} 字`);
}

if (!items.length) {
  console.log("没有采集到任何消息");
  process.exit(0);
}

const cookie = await login();
const res = await fetch(`${API}/api/v1/ingest`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Cookie: cookie },
  body: JSON.stringify({
    platform: PLATFORM,
    items,
    meta: { collector: "feishu-web-dom", version: 2, at: new Date().toISOString(), candidates: threads.length },
  }),
});
console.log(`ingest -> ${res.status} ${(await res.text()).slice(0, 300)}`);
