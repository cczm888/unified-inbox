/**
 * Telegram Bot API 反向代理（Cloudflare Workers）
 *
 * 为什么需要它：境内直连 api.telegram.org 时，TCP 三次握手能成、TLS 握手阶段被 RST，
 * 报 `schannel: failed to receive handshake` / `Recv failure: Connection was reset`。
 * 换 IP、换 SNI、上 IPv6 都一样，是被按 IP 拦的，本地没有绕法。
 *
 * 这个 Worker 做两件事：
 *   1. 把 /bot<token>/<method>、/file/bot<token>/<path> 原样转发到 api.telegram.org；
 *   2. 校验一个共享密钥头，避免变成任何人可用的开放代理（否则别人拿它刷流量、也可能
 *      把你的 token 骗过去）。
 *
 * 部署见 scripts/deploy-telegram-relay.ps1。部署完把地址填进 .env 的 TELEGRAM_API_BASE，
 * 把同一个密钥填进 TELEGRAM_RELAY_KEY。
 */

const UPSTREAM = "https://api.telegram.org";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 密钥只从 Worker 的环境变量读，不写死在代码里
    const key = env.RELAY_KEY || "";
    if (key) {
      const given = request.headers.get("x-relay-key") || url.searchParams.get("k") || "";
      if (given !== key) {
        return new Response("forbidden", { status: 403 });
      }
    }

    // 只放行 Telegram Bot API 的两类路径，其他一律 404：
    //   /bot<token>/<method>        普通方法
    //   /file/bot<token>/<filepath> 下载附件
    if (!/^\/(file\/)?bot[^/]+\//.test(url.pathname)) {
      return new Response("not found", { status: 404 });
    }

    const target = UPSTREAM + url.pathname + url.search;

    // 透传 method / body / content-type；去掉自家密钥头，别泄给上游
    const headers = new Headers(request.headers);
    headers.delete("x-relay-key");
    headers.delete("host");

    const init = {
      method: request.method,
      headers,
      redirect: "follow",
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    };

    const resp = await fetch(target, init);
    const out = new Headers(resp.headers);
    out.set("access-control-allow-origin", "*");
    return new Response(resp.body, { status: resp.status, headers: out });
  },
};
