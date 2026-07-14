import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Default API port — samakan dengan backend uvicorn
const apiPort = process.env.SADT_API_PORT || "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": `http://127.0.0.1:${apiPort}`,
    },
  },
});
