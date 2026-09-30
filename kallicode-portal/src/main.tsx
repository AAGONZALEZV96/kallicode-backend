import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import LimiteError from "./componentes/LimiteError";
import { AuthProvider } from "./estado/auth";
import { I18nProvider } from "./estado/i18n";
import "./estilos/tokens.css";

// El límite raíz queda dentro de I18nProvider para poder traducir la pantalla de recuperación.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <LimiteError nivel="raiz">
        <AuthProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AuthProvider>
      </LimiteError>
    </I18nProvider>
  </StrictMode>,
);
