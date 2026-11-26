/**
 * Ajustes de la organización (KC-PT-10-4), para owner y admin: nombre, idioma por defecto y
 * zona horaria (PATCH /organization). El despliegue, el plan y si la fábrica está encendida se
 * muestran, pero no se cambian aquí.
 *
 * - La zona horaria es la con la que el portal muestra todas las fechas (KC-PT-09-3).
 * - Concurrencia optimista: se guarda con If-Match. Si otra persona cambió los ajustes mientras
 *   se editaban, el backend responde 412: se avisa, se cargan los datos vigentes y no se pisa
 *   lo que guardó la otra persona.
 */
import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { useActualizarOrganizacion, useOrganizacionEditable } from "../api/consultas";
import { codigoDeError } from "../api/errores";
import {
  atributosDeError,
  errorDeTexto,
  erroresDelServidor,
  esErrorDeValidacion,
} from "../api/erroresCampo";
import type { Organizacion as Org } from "../api/tipos";
import { EsqueletoTarjetas } from "../componentes/Esqueleto";
import { ErrorCampo } from "../componentes/ErrorCampo";
import { CajaError } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useI18n } from "../estado/i18n";

const REGLA_NOMBRE = { min: 2, max: 160 };

/** Zonas IANA del navegador, con la vigente aunque el navegador no la conozca. */
function zonasHorarias(vigente: string): string[] {
  const zonas =
    typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  return zonas.includes(vigente) ? zonas : [vigente, ...zonas];
}

export default function Organizacion() {
  const { t } = useI18n();
  const { data, error, refetch } = useOrganizacionEditable();
  // Lo que pasó al guardar: vive aquí porque el formulario se rehace con los datos nuevos.
  const [resultado, setResultado] = useState<"guardado" | "conflicto" | null>(null);

  const cabecera = (
    <div className="view-head">
      <div>
        <h1>{t("org.titulo")}</h1>
        <p>{t("org.sub")}</p>
      </div>
    </div>
  );
  if (error)
    return (
      <div className="view" data-testid="organizacion.vista.raiz">
        {cabecera}
        <CajaError error={error} />
      </div>
    );
  if (!data)
    return (
      <div className="view">
        {cabecera}
        <EsqueletoTarjetas tarjetas={2} />
      </div>
    );
  return (
    <div className="view" data-testid="organizacion.vista.raiz">
      {cabecera}
      {resultado === "conflicto" && (
        <div className="aviso-box" role="alert" data-testid="organizacion.avisos.conflicto">
          {t("org.conflicto")}
        </div>
      )}
      {resultado === "guardado" && (
        <div className="aviso-ok" role="status" data-testid="organizacion.avisos.guardado">
          {t("pf.guardado")}
        </div>
      )}
      <div className="ajustes">
        {/* La clave rehace el formulario con los datos vigentes tras un conflicto o al guardar. */}
        <Formulario
          key={data.etag ?? JSON.stringify(data.org)}
          org={data.org}
          etag={data.etag}
          alConflicto={() => {
            setResultado("conflicto");
            void refetch();
          }}
          alGuardar={() => setResultado("guardado")}
          alEditar={() => setResultado((r) => (r === "guardado" ? null : r))}
        />
        <DatosFijos org={data.org} />
      </div>
    </div>
  );
}

function Formulario({
  org,
  etag,
  alConflicto,
  alGuardar,
  alEditar,
}: {
  org: Org;
  etag: string | null;
  alConflicto: () => void;
  alGuardar: () => void;
  alEditar: () => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const guardar = useActualizarOrganizacion();
  const [nombre, setNombre] = useState(org.nombre);
  const [idioma, setIdioma] = useState(org.idioma);
  const [zona, setZona] = useState(org.timezone);
  const [intentado, setIntentado] = useState(false);

  /** Editar borra el aviso de guardado y el error del servidor. */
  const editando = (fijar: (v: string) => void) => (valor: string) => {
    fijar(valor);
    alEditar();
    guardar.reset();
  };
  const propio = errorDeTexto(t, nombre, REGLA_NOMBRE);
  const servidor = erroresDelServidor(t, guardar.error, (c) =>
    c === "nombre" ? "nombre" : c === "timezone" ? "zona" : null,
  );
  const errorNombre = (intentado ? propio : undefined) ?? servidor.porCampo.nombre;
  const cambios = {
    ...(nombre.trim() !== org.nombre ? { nombre: nombre.trim() } : {}),
    ...(idioma !== org.idioma ? { idioma } : {}),
    ...(zona !== org.timezone ? { timezone: zona } : {}),
  };
  const sinCambios = Object.keys(cambios).length === 0;
  const errorGeneral =
    codigoDeError(guardar.error) === "PRECONDICION_FALLIDA" || esErrorDeValidacion(guardar.error)
      ? null
      : guardar.error;

  function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    if (propio) {
      document.getElementById(`${id}-nombre`)?.focus();
      return;
    }
    guardar.mutate(
      { cambios, etag },
      {
        onSuccess: alGuardar,
        onError: (err) => {
          if (codigoDeError(err) === "PRECONDICION_FALLIDA") alConflicto();
        },
      },
    );
  }

  const atributos = atributosDeError(`${id}-nombre-error`, errorNombre);
  return (
    <form
      className="card modal-form"
      onSubmit={enviar}
      noValidate
      data-testid="organizacion.ajustes.formulario"
    >
      <h2>{t("org.ajustes")}</h2>
      <div>
        <label htmlFor={`${id}-nombre`}>{t("org.nombre")}</label>
        <input
          id={`${id}-nombre`}
          value={nombre}
          maxLength={REGLA_NOMBRE.max}
          onChange={(e) => editando(setNombre)(e.target.value)}
          {...atributos}
          data-testid="organizacion.formulario.nombre"
        />
        <ErrorCampo
          id={`${id}-nombre-error`}
          error={errorNombre}
          testid="organizacion.formulario.error-nombre"
        />
      </div>
      <div>
        <label htmlFor={`${id}-idioma`}>{t("org.idioma")}</label>
        <select
          id={`${id}-idioma`}
          value={idioma}
          onChange={(e) => editando(setIdioma)(e.target.value)}
          aria-describedby={`${id}-idioma-ayuda`}
          data-testid="organizacion.formulario.idioma"
        >
          <option value="es">Español</option>
          <option value="en">English</option>
          <option value="pt">Português</option>
        </select>
        <p id={`${id}-idioma-ayuda`} className="ayuda-campo">
          {t("org.idioma.ayuda")}
        </p>
      </div>
      <div>
        <label htmlFor={`${id}-zona`}>{t("org.zona")}</label>
        <select
          id={`${id}-zona`}
          value={zona}
          onChange={(e) => editando(setZona)(e.target.value)}
          aria-describedby={`${id}-zona-ayuda`}
          data-testid="organizacion.formulario.zona"
        >
          {zonasHorarias(org.timezone).map((z) => (
            <option key={z} value={z}>
              {z.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <p id={`${id}-zona-ayuda`} className="ayuda-campo">
          {t("org.zona.ayuda")}
        </p>
        <ErrorCampo
          id={`${id}-zona-error`}
          error={servidor.porCampo.zona}
          testid="organizacion.formulario.error-zona"
        />
      </div>
      <CajaError error={errorGeneral} />
      <div className="modal-acciones">
        <button
          className="btn primary"
          disabled={guardar.isPending || sinCambios}
          data-testid="organizacion.formulario.guardar"
        >
          {guardar.isPending ? t("comun.cargando") : t("comun.guardar")}
        </button>
      </div>
    </form>
  );
}

function DatosFijos({ org }: { org: Org }) {
  const { t } = useI18n();
  const { tieneAccesoARuta } = usePermiso();
  // Una modalidad que el portal no conoce, tal cual.
  const clave = `org.despliegue.${org.despliegue}`;
  const despliegue = t(clave) === clave ? org.despliegue : t(clave);
  return (
    <section className="card" aria-labelledby="org-fijos" data-testid="organizacion.datos.resumen">
      <h2 id="org-fijos">{t("org.fijos")}</h2>
      <dl className="ajustes-fijos">
        <div>
          <dt>{t("org.plan")}</dt>
          <dd>
            {org.plan?.nombre ?? "—"}
            {org.plan && tieneAccesoARuta("/consumo") && (
              <>
                {" · "}
                <Link to="/consumo" data-testid="organizacion.datos.plan">
                  {t("aviso.ver_consumo")}
                </Link>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>{t("org.despliegue")}</dt>
          <dd>{despliegue}</dd>
        </div>
        <div>
          <dt>{t("org.fabrica")}</dt>
          <dd>{t(org.fabrica_activa ? "org.fabrica.si" : "org.fabrica.no")}</dd>
        </div>
      </dl>
    </section>
  );
}
