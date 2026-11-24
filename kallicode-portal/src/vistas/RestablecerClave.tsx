/**
 * Restablecer la contraseña (KC-PT-10-6), pantalla pública: el código del correo (kcpr_, en la
 * URL como ?token= o pegado a mano, porque hoy el correo trae solo el código) y la contraseña
 * nueva dos veces. Al guardarla, el backend cierra todas las sesiones y se vuelve al login con
 * el aviso.
 */
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import {
  atributosDeError,
  errorDeClaveDelServidor,
  erroresDelServidor,
  primeroConError,
  type ErroresCampo,
} from "../api/erroresCampo";
import { MensajeEsquema, type RestablecerClaveIn } from "../api/tipos";
import { CampoClave, RequisitosClave } from "../componentes/CampoClave";
import { ErrorCampo } from "../componentes/ErrorCampo";
import Publica from "../componentes/Publica";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import { claveCumple } from "../estado/politicaClave";

const CAMPOS = ["codigo", "clave", "confirmar"] as const;
const DEL_BACKEND: Record<string, string> = { token: "codigo", password_nueva: "clave" };

export default function RestablecerClave() {
  const { t } = useI18n();
  const id = useId();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [codigo, setCodigo] = useState(params.get("token") ?? "");
  const [clave, setClave] = useState("");
  const [confirmar, setConfirmar] = useState("");
  const [intentado, setIntentado] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const propios: ErroresCampo = {
    codigo: !codigo.trim().startsWith("kcpr_") ? t("codigo.formato_reset") : undefined,
    clave: !claveCumple(clave) ? t("clave.no_cumple") : undefined,
    confirmar: confirmar !== clave ? t("clave.no_coincide") : undefined,
  };
  const servidor = erroresDelServidor(t, error, (c) => DEL_BACKEND[c] ?? null);
  const errores: ErroresCampo = {
    ...(intentado ? propios : {}),
    ...servidor.porCampo,
    ...(errorDeClaveDelServidor(t, error) ? { clave: errorDeClaveDelServidor(t, error) } : {}),
  };
  // El enlace vencido o usado: se ofrece pedir otro.
  const codigoInvalido = error instanceof ApiError && error.codigo === "AUTH_TOKEN_RESET_INVALIDO";
  /** Al editar, el error del servidor ya no corresponde a lo escrito. */
  const editando = (fijar: (v: string) => void) => (valor: string) => {
    fijar(valor);
    setError(null);
  };
  const errorGeneral = error instanceof ApiError && error.http === 422 ? null : error;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    setError(null);
    const primero = primeroConError(CAMPOS, propios);
    if (primero) {
      document.getElementById(`${id}-${primero}`)?.focus();
      return;
    }
    setEnviando(true);
    try {
      const cuerpo: RestablecerClaveIn = { token: codigo.trim(), password_nueva: clave };
      await api.post("/api/v1/auth/reset-password", cuerpo, { esquema: MensajeEsquema });
      nav("/login", { replace: true, state: { aviso: t("rs.listo") } });
    } catch (err) {
      setError(err);
    } finally {
      setEnviando(false);
    }
  }

  const atributosCodigo = atributosDeError(`${id}-codigo-error`, errores.codigo);
  return (
    <Publica titulo={t("rs.titulo")} testid="restablecer-clave.vista.raiz">
      <form className="publica-cuerpo" onSubmit={enviar} noValidate>
        <p className="publica-sub">{t("rs.sub")}</p>
        <div>
          <label htmlFor={`${id}-codigo`}>{t("rs.codigo")}</label>
          <input
            id={`${id}-codigo`}
            autoComplete="one-time-code"
            spellCheck={false}
            value={codigo}
            onChange={(e) => editando(setCodigo)(e.target.value)}
            aria-invalid={atributosCodigo["aria-invalid"]}
            aria-describedby={[`${id}-codigo-ayuda`, atributosCodigo["aria-describedby"]]
              .filter(Boolean)
              .join(" ")}
            data-testid="restablecer-clave.formulario.codigo"
          />
          <p id={`${id}-codigo-ayuda`} className="ayuda-campo">
            {t("rs.codigo.ayuda")}
          </p>
          <ErrorCampo
            id={`${id}-codigo-error`}
            error={errores.codigo}
            testid="restablecer-clave.formulario.error-codigo"
          />
        </div>
        <CampoClave
          id={`${id}-clave`}
          etiqueta={t("rs.clave")}
          valor={clave}
          alCambiar={editando(setClave)}
          error={errores.clave}
          idRequisitos={`${id}-requisitos`}
          deshabilitado={enviando}
          testid="restablecer-clave.formulario.clave"
        >
          <ErrorCampo
            id={`${id}-clave-error`}
            error={errores.clave}
            testid="restablecer-clave.formulario.error-clave"
          />
        </CampoClave>
        <RequisitosClave
          id={`${id}-requisitos`}
          valor={clave}
          testid="restablecer-clave.formulario.requisitos"
        />
        <CampoClave
          id={`${id}-confirmar`}
          etiqueta={t("rs.confirmar")}
          valor={confirmar}
          alCambiar={setConfirmar}
          error={errores.confirmar}
          deshabilitado={enviando}
          testid="restablecer-clave.formulario.confirmar"
        >
          <ErrorCampo
            id={`${id}-confirmar-error`}
            error={errores.confirmar}
            testid="restablecer-clave.formulario.error-confirmar"
          />
        </CampoClave>
        <CajaError error={errorGeneral} />
        {codigoInvalido && (
          <Link to="/recuperar-clave" data-testid="restablecer-clave.avisos.pedir-otro">
            {t("rs.pedir_otro")}
          </Link>
        )}
        <button
          className="btn primary"
          disabled={enviando}
          data-testid="restablecer-clave.formulario.enviar"
        >
          {enviando ? t("comun.cargando") : t("rs.enviar")}
        </button>
      </form>
      <Link
        className="publica-volver"
        to="/login"
        data-testid="restablecer-clave.formulario.volver"
      >
        {t("pub.volver_login")}
      </Link>
    </Publica>
  );
}
