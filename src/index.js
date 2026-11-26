const BACKEND = "https://orlando-wesley-clocks-fitting.trycloudflare.com";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (
      url.pathname === "/" &&
      ["code", "state", "error"].some((key) => url.searchParams.has(key))
    ) {
      const headers = new Headers();
      const cookie = request.headers.get("cookie");
      if (cookie) headers.set("cookie", cookie);

      try {
        const callbackResponse = await fetch(BACKEND + url.pathname + url.search, {
          headers,
          redirect: "manual",
        });
        const responseHeaders = new Headers(callbackResponse.headers);
        responseHeaders.set("cache-control", "no-store");
        return new Response(callbackResponse.body, {
          status: callbackResponse.status,
          headers: responseHeaders,
        });
      } catch {
        return new Response("Dashboard authentication service is unavailable.", {
          status: 502,
          headers: { "cache-control": "no-store" },
        });
      }
    }

    if (url.pathname === "/" || url.pathname === "/login-preview") {
      const cookie = request.headers.get("cookie");
      const headers = cookie ? { cookie } : {};
      let authResponse;
      try {
        authResponse = await fetch(BACKEND + "/api/auth/me", { headers });
      } catch {
        return new Response("Dashboard authentication service is unavailable.", {
          status: 502,
          headers: { "cache-control": "no-store" },
        });
      }

      if (!authResponse.ok && authResponse.status !== 401) {
        return new Response("Unable to verify the dashboard session.", {
          status: 502,
          headers: { "cache-control": "no-store" },
        });
      }

      const assetPath = authResponse.ok ? "/" : "/login-preview";
      const assetUrl = new URL(assetPath, request.url);
      assetUrl.search = url.search;
      const assetResponse = await env.ASSETS.fetch(new Request(assetUrl, request));
      const responseHeaders = new Headers(assetResponse.headers);
      responseHeaders.set("cache-control", "no-store");
      return new Response(assetResponse.body, {
        status: assetResponse.status,
        headers: responseHeaders,
      });
    }

    if (url.pathname.startsWith("/api/")) {
      const target = BACKEND + url.pathname + url.search;
      const headers = new Headers(request.headers);
      headers.delete("host");

      const init = {
        method: request.method,
        headers,
        redirect: "follow",
      };
      if (!["GET", "HEAD"].includes(request.method)) {
        init.body = request.body;
      }

      const response = await fetch(target, init);
      const responseHeaders = new Headers(response.headers);
      responseHeaders.delete("content-length");
      responseHeaders.set("cache-control", "no-store");
      return new Response(response.body, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    return env.ASSETS.fetch(request);
  },
};
