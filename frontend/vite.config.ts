import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  // Rollup graph analysis stalls on this Windows host with React 19. Keeping
  // every module preserves behavior; esbuild still minifies the final bundle.
  build: { rollupOptions: { treeshake: false, input: { workbench: "index.html", bot: "bot.html" } } },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true },
});
