import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Bibliotecas en archivos aparte (KC-PT-11-1, PT-F20): cambian poco entre despliegues, así que el
 * navegador las conserva en caché aunque cambie el código del portal.
 */
const GRUPOS: [RegExp, string][] = [
  [/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/, "vendor-react"],
  [/[\\/]node_modules[\\/](react-router|react-router-dom|@remix-run)[\\/]/, "vendor-router"],
  [/[\\/]node_modules[\\/]@tanstack[\\/]/, "vendor-query"],
  [/[\\/]node_modules[\\/]zod[\\/]/, "vendor-zod"],
];

// El portal habla con la API local en dev vía proxy (evita CORS en desarrollo).
export default defineConfig({
  plugins: [react()],
  build: {
    // dist/.vite/manifest.json: el presupuesto del paquete se mide sobre él (KC-PT-11-2).
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          return GRUPOS.find(([patron]) => patron.test(id))?.[1];
        },
      },
    },
  },
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
