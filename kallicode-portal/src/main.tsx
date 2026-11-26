import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { crearClienteConsultas } from "./api/consultas";
import App from "./App";
import LimiteError from "./componentes/LimiteError";
import { AuthProvider } from "./estado/auth";
import { cargarIdioma, I18nProvider, idiomaGuardado } from "./estado/i18n";
import "./estilos/tokens.css";

const clienteConsultas = crearClienteConsultas();

// El límite raíz queda dentro de I18nProvider para poder traducir la pantalla de recuperación.
// QueryClientProvider envuelve a AuthProvider: al cerrar sesión se vacía la caché.
function pintar() {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <I18nProvider>
        <LimiteError nivel="raiz">
          <QueryClientProvider client={clienteConsultas}>
            <AuthProvider>
              <BrowserRouter>
                <App />
              </BrowserRouter>
            </AuthProvider>
          </QueryClientProvider>
        </LimiteError>
      </I18nProvider>
    </StrictMode>,
  );
}

// El idioma guardado se descarga antes de pintar: sin esto, quien eligió inglés o portugués
// vería un instante el portal en español (KC-PT-11-1). Si la descarga falla, se pinta en español.
cargarIdioma(idiomaGuardado())
  .catch(() => {})
  .then(() => {
    pintar();
    // Web Vitals (KC-PT-11-2): la biblioteca se descarga después de pintar.
    import("./metricas/webVitals").then((m) => m.iniciarWebVitals()).catch(() => {});
  });
