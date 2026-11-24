/**
 * Recuperar la contraseña (KC-PT-10-6), pantalla pública: pide el correo y el backend envía un
 * código de un solo uso (kcpr_, 30 minutos). La respuesta es siempre la misma, exista o no la
 * cuenta (POST /auth/forgot-password no revela nada), así que la pantalla tampoco.
 */
import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { MensajeEsquema, type RecuperarClaveIn } from "../api/tipos";
import Publica from "../componentes/Publica";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

export default function RecuperarClave() {
  const { t } = useI18n();
  const id = useId();
  const [email, setEmail] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [enviado, setEnviado] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      const cuerpo: RecuperarClaveIn = { email: email.trim() };
      await api.post("/api/v1/auth/forgot-password", cuerpo, { esquema: MensajeEsquema });
      setEnviado(true);
    } catch (err) {
      setError(err);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <Publica titulo={t("rc.titulo")} testid="recuperar-clave.vista.raiz">
      {enviado ? (
        <div className="publica-cuerpo">
          <div className="aviso-ok" role="status" data-testid="recuperar-clave.avisos.enviado">
            {t("rc.enviado")}
          </div>
          <Link
            className="btn primary"
            to="/restablecer-clave"
            data-testid="recuperar-clave.avisos.continuar"
          >
            {t("rc.tengo_codigo")}
          </Link>
        </div>
      ) : (
        <form className="publica-cuerpo" onSubmit={enviar}>
          <p className="publica-sub">{t("rc.sub")}</p>
          <div>
            <label htmlFor={`${id}-correo`}>{t("login.email")}</label>
            <input
              id={`${id}-correo`}
              type="email"
              autoComplete="email"
              value={email}
              required
              // eslint-disable-next-line jsx-a11y/no-autofocus -- pantalla de un solo propósito, como el login
              autoFocus
              onChange={(e) => setEmail(e.target.value)}
              data-testid="recuperar-clave.formulario.correo"
            />
          </div>
          <CajaError error={error} />
          <button
            className="btn primary"
            disabled={enviando}
            data-testid="recuperar-clave.formulario.enviar"
          >
            {enviando ? t("comun.cargando") : t("rc.enviar")}
          </button>
        </form>
      )}
      <Link className="publica-volver" to="/login" data-testid="recuperar-clave.formulario.volver">
        {t("pub.volver_login")}
      </Link>
    </Publica>
  );
}
