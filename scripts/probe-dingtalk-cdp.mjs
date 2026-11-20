/**
 * 用 CDP 连钉钉客户端里的 CEF 页面，验证能不能读到 DOM。
 *
 * 前提：钉钉要以 --remote-debugging-port=9222 启动。
 *   & "C:\Program Files (x86)\DingDing\main\current\DingTalk.exe" --remote-debugging-port=9222
 */
const ENDPOINT = "http://127.0.0.1:9222";

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    }
  });
  const ready = new Promise((resolve, reject) => {
    ws.addEventListener("open", () => resolve());
    ws.addEventListener("error", (e) => reject(new Error("ws error")));
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
      setTimeout(() => {
        if (pending.has(mid)) {
          pending.delete(mid);
          reject(new Error(`${method} timeout`));
        }
      }, 12000);
    });
  return { ws, ready, send };
}

async function main() {
  const list = await (await fetch(`${ENDPOINT}/json/list`)).json();
  const page = list.find((t) => t.url.includes("chatbox")) || list.find((t) => t.type === "page");
  if (!page) {
    console.log(JSON.stringify({ ok: false, error: "没有可用的 target" }));
    return;
  }
  console.log("target:", page.title, "|", page.url.slice(0, 70));

  const { ws, ready, send } = connect(page.webSocketDebuggerUrl);
  await ready;
  await send("Runtime.enable");

  const evaluate = async (expr) => {
    const r = await send("Runtime.evaluate", {
      expression: expr,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text || "eval error");
    return r.result.value;
  };

  const info = await evaluate(`(() => {
    const body = document.body ? (document.body.innerText || "") : "";
    const ce = document.querySelectorAll('[contenteditable="true"]');
    const ta = document.querySelectorAll("textarea");
    return {
      title: document.title,
      url: location.href.slice(0, 80),
      bodyLen: body.length,
      bodyHead: body.split("\\n").map(s => s.trim()).filter(Boolean).slice(0, 18),
      editable: ce.length,
      textarea: ta.length,
      editableInfo: Array.from(ce).slice(0, 4).map(e => {
        const r = e.getBoundingClientRect();
        return { cls: (e.className || "").toString().slice(0, 40), w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.left), y: Math.round(r.top) };
      }),
    };
  })()`);

  console.log(JSON.stringify(info, null, 1));

  // chatbox 可能只是外壳，真正的界面在 iframe 或子 target 里
  const frames = await send("Page.getFrameTree").catch(() => null);
  if (frames) {
    const flat = [];
    const walk = (node, depth) => {
      flat.push({ depth, url: (node.frame.url || "").slice(0, 90), name: node.frame.name || "" });
      (node.childFrames || []).forEach((c) => walk(c, depth + 1));
    };
    walk(frames.frameTree, 0);
    console.log("=== frame 树 ===");
    flat.forEach((f) => console.log("  ".repeat(f.depth) + `- ${f.url} ${f.name ? "name=" + f.name : ""}`));
  }

  const iframes = await evaluate(`(() => {
    const out = [];
    for (const f of document.querySelectorAll("iframe")) {
      const r = f.getBoundingClientRect();
      out.push({ src: (f.src || "").slice(0, 80), w: Math.round(r.width), h: Math.round(r.height) });
    }
    return out;
  })()`);
  console.log("=== iframe ===");
  console.log(JSON.stringify(iframes, null, 1));

  ws.close();
}

main().catch((e) => {
  console.log(JSON.stringify({ ok: false, error: String(e.message || e).slice(0, 200) }));
});
