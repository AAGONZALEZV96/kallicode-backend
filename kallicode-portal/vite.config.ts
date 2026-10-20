import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// El portal habla con la API local en dev vía proxy (evita CORS en desarrollo).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    env: { VITE_API_URL: "http://api.test" },
    deps: {
      optimizer: {
        web: {
          include: ["@exodus/bytes", "html-encoding-sniffer"],
        },
      },
    },
  },
});