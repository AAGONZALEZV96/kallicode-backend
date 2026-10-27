import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { crearClienteConsultas } from "./api/consultas";
import App from "./App";
import LimiteError from "./componentes/LimiteError";
import { AuthProvider } from "./estado/auth";
import { I18nProvider } from "./estado/i18n";
import "./estilos/tokens.css";

const clienteConsultas = crearClienteConsultas();

// El límite raíz queda dentro de I18nProvider para poder traducir la pantalla de recuperación.
// QueryClientProvider envuelve a AuthProvider: al cerrar sesión se vacía la caché.
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
