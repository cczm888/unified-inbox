/**
 * 通过 browser 级 WebSocket 调 Target.getTargets，把所有 target 都列出来
 * （/json/list 只给 page，隐藏的 webview / iframe 要这样翻）。
 */
const ENDPOINT = "http://127.0.0.1:9222";

async function main() {
  const ver = await (await fetch(`${ENDPOINT}/json/version`)).json();
  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();

  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(m.error.message)) : resolve(m.result);
    }
  });

  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
      setTimeout(() => {
        if (pending.has(mid)) {
          pending.delete(mid);
          reject(new Error(method + " timeout"));
        }
      }, 10000);
    });

  await new Promise((r, j) => {
    ws.addEventListener("open", r);
    ws.addEventListener("error", () => j(new Error("ws error")));
  });

  const { targetInfos } = await send("Target.getTargets");
  console.log("=== 全部 target:", targetInfos.length, "===");
  for (const t of targetInfos) {
    console.log(`  [${t.type}] ${(t.title || "").slice(0, 30)} | ${(t.url || "").slice(0, 80)}`);
  }

  // 对每个 page/webview 都试着跑一段 evaluate，看谁的 body 有东西
  const candidates = targetInfos.filter((t) => ["page", "webview", "iframe", "other"].includes(t.type));
  console.log("\n=== 逐个试 Runtime.evaluate ===");
  for (const t of candidates) {
    if (!t.webSocketDebuggerUrl) {
      console.log(`  [${t.type}] ${t.title} → 无 ws url`);
      continue;
    }
    try {
      const r = await send("Target.attachToTarget", { targetId: t.targetId, flatten: true });
      const sid = r.sessionId;
      const ev = await new Promise((resolve, reject) => {
        const mid = ++id;
        pending.set(mid, { resolve, reject });
        ws.send(JSON.stringify({
          id: mid,
          sessionId: sid,
          method: "Runtime.evaluate",
          params: {
            expression: `(() => ({ t: document.title, len: (document.body?.innerText || "").length, head: (document.body?.innerText || "").split("\\n").filter(Boolean).slice(0, 6) }))()`,
            returnByValue: true,
          },
        }));
        setTimeout(() => reject(new Error("eval timeout")), 8000);
      });
      console.log(`  [${t.type}] ${(t.title || "").slice(0, 20)} → len=${ev.result?.value?.len} head=${JSON.stringify(ev.result?.value?.head || [])}`);
    } catch (e) {
      console.log(`  [${t.type}] ${(t.title || "").slice(0, 20)} → ${String(e.message || e).slice(0, 60)}`);
    }
  }
  ws.close();
}

main().catch((e) => console.log("ERR", String(e.message || e).slice(0, 200)));
