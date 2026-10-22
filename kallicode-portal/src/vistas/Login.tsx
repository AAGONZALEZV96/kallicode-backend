/** Login del portal (POST /auth/login). Muestra el sobre de error tal cual. */
import { useState, type FormEvent } from "react";

import { CajaError } from "../componentes/ui";
import { useAuth } from "../estado/auth";
import { useI18n } from "../estado/i18n";

export default function Login() {
  const { login, cargando } = useAuth();
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="login-wrap" data-testid="login.vista.raiz">
      <form className="card login-card" onSubmit={enviar}>
        <div className="logo" style={{ marginBottom: 18 }}>
          <div className="mark">&lt;×&gt;</div>
          <div>
            kallicode<small>{t("login.titulo")}</small>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <label>{t("login.email")}</label>
            <input
              type="email"
              value={email}
              required
              // eslint-disable-next-line jsx-a11y/no-autofocus -- pantalla de un solo propósito: el foco inicial en el correo es lo esperado
              autoFocus
              onChange={(e) => setEmail(e.target.value)}
              data-testid="login.formulario.correo"
            />
          </div>
          <div>
            <label>{t("login.password")}</label>
            <input
              type="password"
              value={password}
              required
              minLength={8}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="login.formulario.clave"
            />
          </div>
          <CajaError error={error} />
          <button className="btn primary" disabled={cargando} data-testid="login.formulario.entrar">
            {cargando ? t("comun.cargando") : t("login.entrar")}
          </button>
        </div>
      </form>
    </div>
  );
}
