import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const apiPort = process.env.SATRIA_API_PORT || process.env.SADT_API_PORT || "8000";
const desktopDev = process.env.SATRIA_DESKTOP === "1";
const uiPort = desktopDev
  ? Number(process.env.SATRIA_DESKTOP_UI_PORT || 5175)
  : Number(process.env.SATRIA_UI_PORT || process.env.SADT_UI_PORT || 5173);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: uiPort,
    strictPort: true,
    proxy: {
      "/api": `http://127.0.0.1:${apiPort}`,
    },
  },
  build: {
    sourcemap: false,
  },
});
