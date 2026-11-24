/**
 * Perfil propio (KC-PT-10-4): nombre e idioma de la cuenta, y cambio de contraseña
 * (PATCH /users/me). El correo y el rol se muestran, pero no se cambian aquí: el correo
 * identifica la cuenta y el rol lo asigna un administrador.
 *
 * - El idioma de la cuenta pasa a ser también el del portal al guardarlo.
 * - Cambiar la contraseña cierra todas las sesiones, también esta (el backend revoca todos los
 *   refresh tokens): se vuelve al login con el aviso, en vez de expulsar al usuario minutos
 *   después sin explicación.
 */
import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { useActualizarPerfil, usePerfil } from "../api/consultas";
import { ApiError } from "../api/client";
import {
  atributosDeError,
  errorDeClaveDelServidor,
  errorDeTexto,
  erroresDelServidor,
  primeroConError,
  type ErroresCampo,
} from "../api/erroresCampo";
import type { PerfilPropio } from "../api/tipos";
import { CampoClave, RequisitosClave } from "../componentes/CampoClave";
import { EsqueletoTarjetas } from "../componentes/Esqueleto";
import { ErrorCampo } from "../componentes/ErrorCampo";
import { CajaError } from "../componentes/ui";
import { useAuth } from "../estado/auth";
import { useI18n, type Idioma } from "../estado/i18n";
import { claveCumple } from "../estado/politicaClave";

const REGLA_NOMBRE = { min: 2, max: 120 };

export default function Perfil() {
  const { t } = useI18n();
  const { data: perfil, error } = usePerfil();

  if (error)
    return (
      <div className="view" data-testid="perfil.vista.raiz">
        <CajaError error={error} />
      </div>
    );
  const cabecera = (
    <div className="view-head">
      <div>
        <h1>{t("pf.titulo")}</h1>
        <p>{t("pf.sub")}</p>
      </div>
    </div>
  );
  // Mientras carga: el título y la silueta (sin vista.raiz, que indica que la vista cargó).
  if (!perfil)
    return (
      <div className="view">
        {cabecera}
        <EsqueletoTarjetas tarjetas={2} />
      </div>
    );
  return (
    <div className="view" data-testid="perfil.vista.raiz">
      {cabecera}
      <div className="ajustes">
        <DatosPerfil perfil={perfil} />
        <CambioClave />
      </div>
    </div>
  );
}

function DatosPerfil({ perfil }: { perfil: PerfilPropio }) {
  const { t, cambiarIdioma } = useI18n();
  const { refrescarUsuario } = useAuth();
  const id = useId();
  const guardar = useActualizarPerfil();
  const [nombre, setNombre] = useState(perfil.nombre);
  const [idioma, setIdioma] = useState(perfil.idioma);
  const [intentado, setIntentado] = useState(false);
  const [guardado, setGuardado] = useState(false);

  const propio = errorDeTexto(t, nombre, REGLA_NOMBRE);
  const servidor = erroresDelServidor(t, guardar.error, (c) => (c === "nombre" ? "nombre" : null));
  const errorNombre = (intentado ? propio : undefined) ?? servidor.porCampo.nombre;
  const cambios = {
    ...(nombre.trim() !== perfil.nombre ? { nombre: nombre.trim() } : {}),
    ...(idioma !== perfil.idioma ? { idioma } : {}),
  };
  const sinCambios = Object.keys(cambios).length === 0;

  function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    setGuardado(false);
    if (propio) {
      document.getElementById(`${id}-nombre`)?.focus();
      return;
    }
    guardar.mutate(cambios, {
      onSuccess: (nuevo) => {
        refrescarUsuario(nuevo);
        cambiarIdioma(nuevo.idioma as Idioma);
        setGuardado(true);
      },
    });
  }

  const atributos = atributosDeError(`${id}-nombre-error`, errorNombre);
  return (
    <form
      className="card modal-form"
      onSubmit={enviar}
      noValidate
      data-testid="perfil.datos.formulario"
    >
      <h2>{t("pf.datos")}</h2>
      <dl className="ajustes-fijos">
        <div>
          <dt>{t("login.email")}</dt>
          <dd data-testid="perfil.datos.correo">{perfil.email}</dd>
        </div>
        <div>
          <dt>{t("pf.rol")}</dt>
          <dd data-testid="perfil.datos.rol">{t(`rol.${perfil.rol}`)}</dd>
        </div>
      </dl>
      <div>
        <label htmlFor={`${id}-nombre`}>{t("ai.nombre")}</label>
        <input
          id={`${id}-nombre`}
          autoComplete="name"
          value={nombre}
          maxLength={REGLA_NOMBRE.max}
          onChange={(e) => {
            setNombre(e.target.value);
            setGuardado(false);
          }}
          {...atributos}
          data-testid="perfil.datos.nombre"
        />
        <ErrorCampo
          id={`${id}-nombre-error`}
          error={errorNombre}
          testid="perfil.datos.error-nombre"
        />
      </div>
      <div>
        <label htmlFor={`${id}-idioma`}>{t("ai.idioma")}</label>
        <select
          id={`${id}-idioma`}
          value={idioma}
          onChange={(e) => {
            setIdioma(e.target.value);
            setGuardado(false);
          }}
          aria-describedby={`${id}-idioma-ayuda`}
          data-testid="perfil.datos.idioma"
        >
          <option value="es">Español</option>
          <option value="en">English</option>
          <option value="pt">Português</option>
        </select>
        <p id={`${id}-idioma-ayuda`} className="ayuda-campo">
          {t("pf.idioma.ayuda")}
        </p>
      </div>
      {!servidor.porCampo.nombre && <CajaError error={guardar.error} />}
      {guardado && (
        <div className="aviso-ok" role="status" data-testid="perfil.datos.guardado">
          {t("pf.guardado")}
        </div>
      )}
      <div className="modal-acciones">
        <button
          className="btn primary"
          disabled={guardar.isPending || sinCambios}
          data-testid="perfil.datos.guardar"
        >
          {guardar.isPending ? t("comun.cargando") : t("comun.guardar")}
        </button>
      </div>
    </form>
  );
}

const CAMPOS_CLAVE = ["actual", "nueva", "confirmar"] as const;

function CambioClave() {
  const { t } = useI18n();
  const { logout } = useAuth();
  const nav = useNavigate();
  const id = useId();
  const guardar = useActualizarPerfil();
  const [actual, setActual] = useState("");
  const [nueva, setNueva] = useState("");
  const [confirmar, setConfirmar] = useState("");
  const [intentado, setIntentado] = useState(false);

  const propios: ErroresCampo = {
    actual: actual ? undefined : t("campo.requerido"),
    nueva: claveCumple(nueva) ? undefined : t("clave.no_cumple"),
    confirmar: confirmar === nueva ? undefined : t("clave.no_coincide"),
  };
  const actualIncorrecta =
    guardar.error instanceof ApiError && guardar.error.codigo === "PASSWORD_ACTUAL_INCORRECTA";
  const errorNueva = errorDeClaveDelServidor(t, guardar.error);
  const errores: ErroresCampo = {
    ...(intentado ? propios : {}),
    ...(actualIncorrecta ? { actual: t("pf.actual_incorrecta") } : {}),
    ...(errorNueva ? { nueva: errorNueva } : {}),
  };
  const errorGeneral = actualIncorrecta || errorNueva ? null : guardar.error;

  const editando = (fijar: (v: string) => void) => (valor: string) => {
    fijar(valor);
    guardar.reset();
  };

  function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    const primero = primeroConError(CAMPOS_CLAVE, propios);
    if (primero) {
      document.getElementById(`${id}-${primero}`)?.focus();
      return;
    }
    guardar.mutate(
      { password_actual: actual, password_nueva: nueva },
      {
        onSuccess: () => {
          // El backend cerró todas las sesiones: se vuelve a entrar con la nueva.
          logout();
          nav("/login", { replace: true, state: { aviso: t("pf.clave_cambiada") } });
        },
      },
    );
  }

  const atributosActual = atributosDeError(`${id}-actual-error`, errores.actual);
  return (
    <form
      className="card modal-form"
      onSubmit={enviar}
      noValidate
      data-testid="perfil.clave.formulario"
    >
      <h2>{t("pf.clave")}</h2>
      <p className="note">{t("pf.clave.aviso")}</p>
      <div>
        <label htmlFor={`${id}-actual`}>{t("pf.clave.actual")}</label>
        <input
          id={`${id}-actual`}
          type="password"
          autoComplete="current-password"
          value={actual}
          onChange={(e) => editando(setActual)(e.target.value)}
          {...atributosActual}
          data-testid="perfil.clave.actual"
        />
        <ErrorCampo
          id={`${id}-actual-error`}
          error={errores.actual}
          testid="perfil.clave.error-actual"
        />
      </div>
      <CampoClave
        id={`${id}-nueva`}
        etiqueta={t("rs.clave")}
        valor={nueva}
        alCambiar={editando(setNueva)}
        error={errores.nueva}
        idRequisitos={`${id}-requisitos`}
        testid="perfil.clave.nueva"
      >
        <ErrorCampo
          id={`${id}-nueva-error`}
          error={errores.nueva}
          testid="perfil.clave.error-nueva"
        />
      </CampoClave>
      <RequisitosClave id={`${id}-requisitos`} valor={nueva} testid="perfil.clave.requisitos" />
      <CampoClave
        id={`${id}-confirmar`}
        etiqueta={t("rs.confirmar")}
        valor={confirmar}
        alCambiar={setConfirmar}
        error={errores.confirmar}
        testid="perfil.clave.confirmar"
      >
        <ErrorCampo
          id={`${id}-confirmar-error`}
          error={errores.confirmar}
          testid="perfil.clave.error-confirmar"
        />
      </CampoClave>
      <CajaError error={errorGeneral} />
      <div className="modal-acciones">
        <button
          className="btn primary"
          disabled={guardar.isPending}
          data-testid="perfil.clave.cambiar"
        >
          {guardar.isPending ? t("comun.cargando") : t("pf.clave.cambiar")}
        </button>
      </div>
    </form>
  );
}
