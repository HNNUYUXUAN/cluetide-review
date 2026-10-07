export function allowedPreviewRequest(host, origin, fetchSite) {
  if (!host || !["127.0.0.1:5187", "127.0.0.1:4187"].includes(host)) return false;
  if (fetchSite === "cross-site") return false;
  return !origin || origin === `http://${host}`;
}

export const apiProxy = {
  "/api": { target: "http://127.0.0.1:5186", changeOrigin: true, headers: { Origin: "http://127.0.0.1:5186" } },
};

export function previewOriginGuard() {
  const install = server => {
    server.middlewares.use((request, response, next) => {
      if (!request.url?.startsWith("/api")) return next();
      const header = name => typeof request.headers[name] === "string" ? request.headers[name] : undefined;
      if (!allowedPreviewRequest(header("host"), header("origin"), header("sec-fetch-site"))) {
        response.writeHead(403, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "Same-origin preview request required" })); return;
      }
      next();
    });
  };
  return { name: "cluetide-preview-origin", configureServer: install, configurePreviewServer: install };
}
