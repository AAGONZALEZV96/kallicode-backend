/**
 * Aceptar una invitación (KC-PT-10-6), pantalla pública: el código del correo (kcin_, en la URL
 * como ?token= o pegado a mano), el nombre, la contraseña dos veces y el idioma. El backend crea
 * la cuenta y responde con la sesión abierta: se entra directo al portal.
 *
 * - El idioma elegido cambia la pantalla al instante y se guarda en la cuenta.
 * - Con otra sesión abierta en el navegador no se ofrece el formulario: aceptar abriría la
 *   cuenta nueva encima de la anterior. Primero se cierra la sesión.
 */
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { codigoDeError } from "../api/errores";
import {
  atributosDeError,
  errorDeClaveDelServidor,
  errorDeTexto,
  erroresDelServidor,
  esErrorDeValidacion,
  primeroConError,
  type ErroresCampo,
} from "../api/erroresCampo";
import { CampoClave, RequisitosClave } from "../componentes/CampoClave";
import { ErrorCampo } from "../componentes/ErrorCampo";
import Publica from "../componentes/Publica";
import { CajaError } from "../componentes/ui";
import { useAuth } from "../estado/auth";
import { esIdioma, useI18n } from "../estado/i18n";
import { claveCumple } from "../estado/politicaClave";

const CAMPOS = ["codigo", "nombre", "clave", "confirmar"] as const;
const DEL_BACKEND: Record<string, string> = {
  token: "codigo",
  nombre: "nombre",
  password: "clave",
};
const REGLA_NOMBRE = { min: 2, max: 120 };

export default function AceptarInvitacion() {
  const { t, idioma, cambiarIdioma } = useI18n();
  const { usuario, cargando, aceptarInvitacion, logout } = useAuth();
  const id = useId();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [codigo, setCodigo] = useState(params.get("token") ?? "");
  const [nombre, setNombre] = useState("");
  const [clave, setClave] = useState("");
  const [confirmar, setConfirmar] = useState("");
  const [intentado, setIntentado] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Se aceptó en esta pantalla: la sesión nueva no es «otra sesión abierta».
  const [aceptada, setAceptada] = useState(false);

  const propios: ErroresCampo = {
    codigo: !codigo.trim().startsWith("kcin_") ? t("codigo.formato_inv") : undefined,
    nombre: errorDeTexto(t, nombre, REGLA_NOMBRE),
    clave: !claveCumple(clave) ? t("clave.no_cumple") : undefined,
    confirmar: confirmar !== clave ? t("clave.no_coincide") : undefined,
  };
  const servidor = erroresDelServidor(t, error, (c) => DEL_BACKEND[c] ?? null);
  const errorClave = errorDeClaveDelServidor(t, error);
  const errores: ErroresCampo = {
    ...(intentado ? propios : {}),
    ...servidor.porCampo,
    ...(errorClave ? { clave: errorClave } : {}),
  };
  const yaExiste = codigoDeError(error) === "USUARIO_YA_EXISTE";
  const errorGeneral = esErrorDeValidacion(error) ? null : error;

  const editando = (fijar: (v: string) => void) => (valor: string) => {
    fijar(valor);
    setError(null);
  };

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    setError(null);
    const primero = primeroConError(CAMPOS, propios);
    if (primero) {
      document.getElementById(`${id}-${primero}`)?.focus();
      return;
    }
    try {
      setAceptada(true);
      await aceptarInvitacion({
        token: codigo.trim(),
        nombre: nombre.trim(),
        password: clave,
        idioma,
      });
      nav("/", { replace: true });
    } catch (err) {
      setAceptada(false);
      setError(err);
    }
  }

  if (usuario && !aceptada)
    return (
      <Publica titulo={t("ai.titulo")} testid="aceptar-invitacion.vista.raiz">
        <div className="publica-cuerpo">
          <div className="aviso-box" role="status" data-testid="aceptar-invitacion.avisos.sesion">
            {t("ai.sesion", { nombre: usuario.nombre })}
          </div>
          <button
            type="button"
            className="btn primary"
            onClick={logout}
            data-testid="aceptar-invitacion.avisos.cerrar-sesion"
          >
            {t("ai.cerrar_sesion")}
          </button>
        </div>
      </Publica>
    );

  const atributosCodigo = atributosDeError(`${id}-codigo-error`, errores.codigo);
  const atributosNombre = atributosDeError(`${id}-nombre-error`, errores.nombre);
  return (
    <Publica titulo={t("ai.titulo")} testid="aceptar-invitacion.vista.raiz">
      <form className="publica-cuerpo" onSubmit={enviar} noValidate>
        <p className="publica-sub">{t("ai.sub")}</p>
        <div>
          <label htmlFor={`${id}-codigo`}>{t("ai.codigo")}</label>
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
            data-testid="aceptar-invitacion.formulario.codigo"
          />
          <p id={`${id}-codigo-ayuda`} className="ayuda-campo">
            {t("ai.codigo.ayuda")}
          </p>
          <ErrorCampo
            id={`${id}-codigo-error`}
            error={errores.codigo}
            testid="aceptar-invitacion.formulario.error-codigo"
          />
        </div>
        <div>
          <label htmlFor={`${id}-nombre`}>{t("ai.nombre")}</label>
          <input
            id={`${id}-nombre`}
            autoComplete="name"
            value={nombre}
            maxLength={REGLA_NOMBRE.max}
            onChange={(e) => editando(setNombre)(e.target.value)}
            {...atributosNombre}
            data-testid="aceptar-invitacion.formulario.nombre"
          />
          <ErrorCampo
            id={`${id}-nombre-error`}
            error={errores.nombre}
            testid="aceptar-invitacion.formulario.error-nombre"
          />
        </div>
        <CampoClave
          id={`${id}-clave`}
          etiqueta={t("login.password")}
          valor={clave}
          alCambiar={editando(setClave)}
          error={errores.clave}
          idRequisitos={`${id}-requisitos`}
          deshabilitado={cargando}
          testid="aceptar-invitacion.formulario.clave"
        >
          <ErrorCampo
            id={`${id}-clave-error`}
            error={errores.clave}
            testid="aceptar-invitacion.formulario.error-clave"
          />
        </CampoClave>
        <RequisitosClave
          id={`${id}-requisitos`}
          valor={clave}
          testid="aceptar-invitacion.formulario.requisitos"
        />
        <CampoClave
          id={`${id}-confirmar`}
          etiqueta={t("rs.confirmar")}
          valor={confirmar}
          alCambiar={setConfirmar}
          error={errores.confirmar}
          deshabilitado={cargando}
          testid="aceptar-invitacion.formulario.confirmar"
        >
          <ErrorCampo
            id={`${id}-confirmar-error`}
            error={errores.confirmar}
            testid="aceptar-invitacion.formulario.error-confirmar"
          />
        </CampoClave>
        <div>
          <label htmlFor={`${id}-idioma`}>{t("ai.idioma")}</label>
          <select
            id={`${id}-idioma`}
            value={idioma}
            onChange={(e) => {
              if (esIdioma(e.target.value)) cambiarIdioma(e.target.value);
            }}
            data-testid="aceptar-invitacion.formulario.idioma"
          >
            <option value="es">Español</option>
            <option value="en">English</option>
            <option value="pt">Português</option>
          </select>
        </div>
        <CajaError error={errorGeneral} />
        {yaExiste && (
          <Link to="/login" data-testid="aceptar-invitacion.avisos.ir-login">
            {t("ai.ir_login")}
          </Link>
        )}
        <button
          className="btn primary"
          disabled={cargando}
          data-testid="aceptar-invitacion.formulario.enviar"
        >
          {cargando ? t("comun.cargando") : t("ai.enviar")}
        </button>
      </form>
    </Publica>
  );
}
