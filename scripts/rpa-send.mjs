/**
 * RPA 发送：用浏览器自动化在飞书网页版里"打字 + 回车"。
 *
 * 为什么需要它：
 *   官方宣讲会明确允许「无 API 时用 RPA 模拟鼠标键盘操作拷贝消息并发送」。
 *   飞书应用身份只能发给"跟机器人有关系的会话"，发给人做不到；
 *   网页版是用户身份，能发给任何联系人——这就是 RPA 的价值。
 *
 * 流程：
 *   1. 登录后端拿 cookie
 *   2. GET /api/v1/rpa/pending 取待发任务
 *   3. 打开飞书网页版，点进目标会话
 *   4. 找到输入框，把文本敲进去，回车
 *   5. POST /api/v1/rpa/ack 回报
 *
 * 现实约束（必须知道）：
 *   飞书网页版对"机器人通知类会话"不渲染输入框，所以这个脚本只能在
 *   真正可对话的会话上工作（对方是真人、或支持对话的机器人）。
 *   找不到输入框时脚本会如实 ack 失败，而不是假装成功。
 *
 * 用法：ego-browser nodejs -e "await import('file:///.../rpa-send.mjs')"
 */

const API = "http://127.0.0.1:8000";
const PASSWORD = "94o32jglny1a";
const DIR = "C:/Users/75090/Desktop/DEEP SEEK/_ego/feishu-setup-20261102-1750/";

/** 用 fetch 拿会话 cookie（ego 的 Node 环境有全局 fetch） */
async function login() {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ password: PASSWORD }),
    redirect: "manual",
  });
  const raw = res.headers.get("set-cookie") || "";
  const m = raw.match(/uti_auth=([^;]+)/);
  return m ? `uti_auth=${m[1]}` : null;
}

async function api(cookie, path, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", Cookie: cookie, ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

/** 在当前页面找输入框：先 contenteditable，再 textarea */
async function findEditor(page) {
  return page.evaluate(() => {
    const cands = [];
    for (const e of document.querySelectorAll('[contenteditable="true"], textarea')) {
      const r = e.getBoundingClientRect();
      if (r.width > 120 && r.height > 16 && r.bottom > 400) {
        cands.push({
          tag: e.tagName,
          cls: String(e.className).slice(0, 60),
          rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
          cx: Math.round(r.left + r.width / 2),
          cy: Math.round(r.top + Math.min(r.height / 2, 20)),
        });
      }
    }
    return cands;
  });
}

/**
 * 点会话：不用 `loc=text=`，因为它会同时匹配「会话名」和「聊天头部标题」，
 * ego 遇到多匹配直接报错。改成按几何找左栏里含该文本的行，点行中心。
 */
async function openChat(page, name) {
  const hit = await page.evaluate((n) => {
    for (const e of document.querySelectorAll("div[class]")) {
      const r = e.getBoundingClientRect();
      if (r.left < 150 || r.left > 220 || r.width < 350 || r.height < 45 || r.height > 90) continue;
      const t = (e.innerText || "").replace(/\s+/g, "");
      if (t.includes(n.replace(/\s+/g, ""))) {
        return { cx: Math.round(r.left + r.width / 2), cy: Math.round(r.top + r.height / 2), txt: t.slice(0, 30) };
      }
    }
    return null;
  }, name);
  if (!hit) return { ok: false, error: `左栏找不到会话「${name}」` };
  await page.evaluate(({ cx, cy }) => {
    const el = document.elementFromPoint(cx, cy);
    if (el) el.click();
  }, hit);
  await page.waitForTimeout(2600);
  return { ok: true, hit };
}

/** 往内容可编辑区域打字：先聚焦，再用 execCommand 插入（React 受控也能收） */
async function typeInto(page, target, text) {
  await page.evaluate(
    ({ cx, cy, text }) => {
      const el = document.elementFromPoint(cx, cy);
      if (!el) return "no element";
      el.focus();
      // contenteditable：用 insertText 走原生输入路径，React 的 onInput 会触发
      const ok = document.execCommand("insertText", false, text);
      if (!ok) {
        el.textContent = text;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return "typed";
    },
    { cx: target.cx, cy: target.cy, text },
  );
  await page.waitForTimeout(600);
}

/** 回车发送 */
async function pressEnter(page, target) {
  await page.evaluate(({ cx, cy }) => {
    const el = document.elementFromPoint(cx, cy);
    if (!el) return;
    el.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      el.dispatchEvent(
        new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }),
      );
    }
  }, { cx: target.cx, cy: target.cy });
  await page.waitForTimeout(1800);
}

const cookie = await login();
if (!cookie) {
  console.log(JSON.stringify({ error: "登录失败，检查 ACCESS_PASSWORD" }));
} else {
  const pend = await api(cookie, "/api/v1/rpa/pending?limit=5");
  console.log(`pending=${pend.count}`);
  if (!pend.count) {
    console.log(JSON.stringify({ done: true, sent: 0, reason: "没有待发任务" }));
  } else {
    const task = await taskSpace("oauth-setup");
    const page = task.page("p1");
    await page.goto("https://www.feishu.cn/messenger/", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(9000);

    let sent = 0;
    const report = [];
    for (const item of pend.items) {
      const text = (item.payload && item.payload.text) || "";
      const chat = item.target_chat || "";
      // target_chat 里存的是会话名（RPA 场景下没有 oc_xxx）
      const opened = await openChat(page, chat);
      if (!opened.ok) {
        report.push({ id: item.id, ok: false, error: opened.error });
        await api(cookie, "/api/v1/rpa/ack", {
          method: "POST",
          body: JSON.stringify({ outbox_id: item.id, ok: false, error: opened.error }),
        });
        continue;
      }
      const editors = await findEditor(page);
      if (!editors.length) {
        const err = "这个会话没有可编辑的输入框（机器人通知类会话不可回复）";
        report.push({ id: item.id, ok: false, error: err });
        await api(cookie, "/api/v1/rpa/ack", {
          method: "POST",
          body: JSON.stringify({ outbox_id: item.id, ok: false, error: err }),
        });
        continue;
      }

      const target = editors[0];
      await typeInto(page, target, text);
      await page.screenshot({ path: `${DIR}rpa-typed-${item.id}.png` });
      await pressEnter(page, target);
      await page.screenshot({ path: `${DIR}rpa-sent-${item.id}.png` });

      // 用时间戳当平台消息 ID：RPA 拿不到真实 ID，但要保证唯一
      const mid = `rpa:${item.id}:${Date.now()}`;
      await api(cookie, "/api/v1/rpa/ack", {
        method: "POST",
        body: JSON.stringify({ outbox_id: item.id, ok: true, platform_msg_id: mid }),
      });
      sent += 1;
      report.push({ id: item.id, ok: true, chat, chars: text.length });
    }
    console.log(JSON.stringify({ done: true, sent, report }, null, 1).slice(0, 1200));
  }
}
