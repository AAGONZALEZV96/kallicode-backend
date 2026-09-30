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
    // fetch de Node no acepta rutas relativas: en pruebas la API tiene origen absoluto
    // y los dobles de MSW la interceptan (src/mocks/handlers.ts).
    env: { VITE_API_URL: "http://api.test" },
  },
});
