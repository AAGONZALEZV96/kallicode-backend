/**
 * Avisos de cuota en todo el portal (KC-PT-09-5), sobre GET /usage/summary:
 *
 * - LOC del ciclo al 80 % o agotada: al agotarse, los tickets nuevos quedan en cola
 *   (en_cola_por_cuota) hasta la renovación; con la cuota agotada dice cuántos esperan.
 * - Tickets en cola con la cuota ya disponible (p. ej. tras mejorar el plan): siguen esperando
 *   la renovación, que es lo que los libera (billing_cycle).
 * - Almacenamiento de evidencia al 80 % o agotado: al llegar al límite no se suben adjuntos.
 *
 * El aviso del 80 % se puede ocultar durante la sesión (vuelve en el ciclo siguiente o si pasa a
 * agotado); el de agotado no. Si la consulta falla o carga, no se muestra nada.
 */
import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { useResumenConsumo } from "../api/consultas";
import type { Umbral, UsageSummary } from "../api/tipos";
import { usePermiso } from "../estado/auth";
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";

const RUTA_COLA = "/tickets?etapa=en_cola_por_cuota";

/**
 * Clave de sesión del aviso del 80 % oculto, por cuota y ciclo. Solo ese aviso se oculta: el de
 * agotado se muestra aunque la clave exista.
 */
const claveOculto = (cuota: string, ciclo: string) => `kc_aviso_oculto:${cuota}:${ciclo}`;

function leerOculto(clave: string): boolean {
  try {
    return sessionStorage.getItem(clave) === "1";
  } catch {
    return false;
  }
}

export default function AvisosCuota() {
  const { data: resumen } = useResumenConsumo();
  if (!resumen) return null;
  return <Avisos resumen={resumen} />;
}

function Avisos({ resumen }: { resumen: UsageSummary }) {
  const { t } = useI18n();
  const fmt = useFormato();
  // Ocultos en esta carga de la página, por si sessionStorage no está disponible.
  const [ocultos, setOcultos] = useState<ReadonlySet<string>>(new Set());

  const renovacion = resumen.plan ? fmt.dia(resumen.plan.renueva_el) : null;
  const { almacenamiento: alm, cola } = resumen;

  function ocultar(clave: string) {
    try {
      sessionStorage.setItem(clave, "1");
    } catch {
      // Sin almacenamiento de sesión el aviso se oculta igual hasta recargar.
    }
    setOcultos((previos) => new Set(previos).add(clave));
  }
  const oculto = (clave: string) => ocultos.has(clave) || leerOculto(clave);

  const avisos = [];

  // LOC del ciclo.
  if (resumen.umbral !== "normal") {
    const clave = claveOculto("loc", resumen.ciclo);
    if (resumen.umbral === "agotado" || !oculto(clave)) {
      const texto =
        resumen.umbral === "aviso_80"
          ? t("aviso.loc.80", {
              pct: fmt.porcentaje(resumen.loc.porcentaje),
              fecha: renovacion ?? "—",
            })
          : renovacion
            ? t("aviso.loc.agotado", { fecha: renovacion })
            : t("aviso.loc.sin_plan");
      avisos.push(
        <Aviso
          key="loc"
          tipo={resumen.umbral}
          testid="layout.avisos.cuota"
          boton={
            resumen.umbral === "aviso_80" && (
              <BotonOcultar testid="layout.avisos.cuota-ocultar" alOcultar={() => ocultar(clave)} />
            )
          }
        >
          {texto}
          {resumen.umbral === "agotado" && cola.tickets > 0 && (
            <>
              {" "}
              {t("aviso.cola.cuantos", { n: fmt.numero(cola.tickets), desde: fmt.dia(cola.desde) })}
            </>
          )}{" "}
          {resumen.umbral === "agotado" && cola.tickets > 0 ? (
            <Enlace ruta={RUTA_COLA} testid="layout.avisos.cuota-enlace">
              {t("aviso.ver_cola")}
            </Enlace>
          ) : (
            <Enlace ruta="/consumo" testid="layout.avisos.cuota-enlace">
              {t("aviso.ver_consumo")}
            </Enlace>
          )}
        </Aviso>,
      );
    }
  }

  // Tickets que siguen en cola aunque la cuota ya no esté agotada: esperan la renovación.
  if (resumen.umbral !== "agotado" && cola.tickets > 0) {
    avisos.push(
      <Aviso key="cola" tipo="aviso_80" testid="layout.avisos.cola">
        {t("aviso.cola", {
          n: fmt.numero(cola.tickets),
          desde: fmt.dia(cola.desde),
          fecha: renovacion ?? "—",
        })}{" "}
        <Enlace ruta={RUTA_COLA} testid="layout.avisos.cola-enlace">
          {t("aviso.ver_cola")}
        </Enlace>
      </Aviso>,
    );
  }

  // Almacenamiento de evidencia.
  if (alm.umbral !== "normal") {
    const clave = claveOculto("almacenamiento", resumen.ciclo);
    if (alm.umbral === "agotado" || !oculto(clave)) {
      const datos = {
        pct: fmt.porcentaje(alm.porcentaje ?? 100),
        usados: fmt.bytes(alm.usados_bytes),
        limite: fmt.bytes(alm.limite_bytes),
      };
      avisos.push(
        <Aviso
          key="almacenamiento"
          tipo={alm.umbral}
          testid="layout.avisos.almacenamiento"
          boton={
            alm.umbral === "aviso_80" && (
              <BotonOcultar
                testid="layout.avisos.almacenamiento-ocultar"
                alOcultar={() => ocultar(clave)}
              />
            )
          }
        >
          {t(alm.umbral === "aviso_80" ? "aviso.alm.80" : "aviso.alm.agotado", datos)}{" "}
          <Enlace ruta="/consumo" testid="layout.avisos.almacenamiento-enlace">
            {t("aviso.ver_consumo")}
          </Enlace>
        </Aviso>,
      );
    }
  }

  return avisos.length ? <div className="avisos-cuota">{avisos}</div> : null;
}

function Aviso({
  tipo,
  testid,
  boton,
  children,
}: {
  tipo: Exclude<Umbral, "normal">;
  testid: string;
  /** El botón para ocultarlo (solo el aviso del 80 %). */
  boton?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className={tipo === "agotado" ? "error-box aviso-cuota" : "aviso-box aviso-cuota"}
      role="status"
      data-testid={testid}
    >
      <p>{children}</p>
      {boton}
    </div>
  );
}

function BotonOcultar({ testid, alOcultar }: { testid: string; alOcultar: () => void }) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      className="aviso-ocultar"
      onClick={alOcultar}
      aria-label={t("aviso.ocultar")}
      title={t("aviso.ocultar")}
      data-testid={testid}
    >
      <span aria-hidden="true">×</span>
    </button>
  );
}

/** Enlace de un aviso, si el usuario puede abrir esa ruta y no está ya en ella. */
function Enlace({ ruta, testid, children }: { ruta: string; testid: string; children: ReactNode }) {
  const { pathname, search } = useLocation();
  const { tieneAccesoARuta } = usePermiso();
  if (!tieneAccesoARuta(ruta.split("?")[0]!) || pathname + search === ruta) return null;
  return (
    <Link to={ruta} data-testid={testid}>
      {children}
    </Link>
  );
}
