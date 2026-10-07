import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { apiProxy, previewOriginGuard } from "./preview-proxy.mjs";

export default defineConfig({
  base: "./",
  plugins: [previewOriginGuard(), react()],
  // Rollup graph analysis stalls on this Windows host with React 19. Keeping
  // every module preserves behavior; esbuild still minifies the final bundle.
  build: { rollupOptions: { treeshake: false } },
  server: {
    host: "127.0.0.1",
    port: 5187,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: { host: "127.0.0.1", port: 4187, strictPort: true, proxy: apiProxy },
});
