/**
 * Asistente de onboarding de 5 pasos (KC-PT-07-1): navegación, progreso y reanudación.
 *
 * - El paso actual va en la URL (?paso=1…5): se puede recargar, compartir y volver atrás.
 *   Sin paso en la URL, retoma en el primer paso obligatorio sin hacer (pasos.ts).
 * - El progreso se deduce de lo que el backend ya tiene: conexiones, mapa de ramas,
 *   documentos del grafo y fábrica activa.
 * - Al cambiar de paso, el foco va al título del paso nuevo.
 * Los formularios de cada paso llegan con KC-PT-07-2 a KC-PT-07-5; por ahora cada paso
 * resume lo configurado y enlaza a Conexiones.
 */
import { useEffect, useId, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useConexiones, useEstadoGrafo, useOrganizacion } from "../api/consultas";
import { CajaError, Cargando } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import ContenidoPaso from "./onboarding/ContenidoPaso";
import { estadoAsistente, OPCIONALES, PASOS, pasoDeUrl, type Paso } from "./onboarding/pasos";

/** Pasos que ya se configuran dentro del asistente: no llevan el enlace a Conexiones. */
const PASOS_EN_ASISTENTE: ReadonlySet<Paso> = new Set<Paso>([
  "repositorio",
  "herramientas",
  "ramas",
]);

export default function Onboarding() {
  const { t } = useI18n();
  const conexiones = useConexiones();
  const organizacion = useOrganizacion();
  const grafo = useEstadoGrafo();
  const [parametros, setParametros] = useSearchParams();
  const pasoPedido = parametros.get("paso");
  const titulo = useRef<HTMLHeadingElement>(null);
  const moverFoco = useRef(false);
  const idProgreso = useId();
  const idTitulo = useId();

  // El foco va al título solo cuando el cambio de paso lo pidió la persona.
  useEffect(() => {
    if (!moverFoco.current) return;
    moverFoco.current = false;
    titulo.current?.focus();
  }, [pasoPedido]);

  const error = conexiones.error ?? organizacion.error;
  if (!conexiones.data || !organizacion.data) {
    return error ? (
      <div className="view" data-testid="onboarding.vista.raiz">
        <CajaError error={error} />
      </div>
    ) : (
      <Cargando />
    );
  }

  const datos = {
    conexiones: conexiones.data,
    grafo: grafo.data,
    fabricaActiva: organizacion.data.fabrica_activa,
  };
  const estado = estadoAsistente(datos);
  const indice = pasoDeUrl(pasoPedido) ?? estado.reanudar;
  const paso = PASOS[indice]!;
  const total = PASOS.length;

  function ir(destino: number) {
    moverFoco.current = true;
    setParametros({ paso: String(destino + 1) });
  }

  return (
    <div className="view" data-testid="onboarding.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("asis.titulo")}</h1>
          <p>{t("asis.sub")}</p>
        </div>
      </div>

      <div className="card asistente">
        <div className="asistente-progreso">
          <p id={idProgreso}>
            {t("asis.progreso", { n: String(estado.cantidadCompletos), total: String(total) })}
          </p>
          <div
            className="asistente-barra"
            role="progressbar"
            aria-labelledby={idProgreso}
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={estado.cantidadCompletos}
            data-testid="onboarding.progreso.barra"
          >
            <div style={{ width: `${(estado.cantidadCompletos / total) * 100}%` }} />
          </div>
        </div>

        <nav aria-label={t("asis.pasos")}>
          <ol className="asistente-pasos">
            {PASOS.map((p, i) => {
              const completo = estado.completos[p];
              return (
                <li key={p}>
                  <button
                    type="button"
                    className={`asistente-paso${i === indice ? " actual" : ""}${completo ? " completo" : ""}`}
                    aria-current={i === indice ? "step" : undefined}
                    onClick={() => ir(i)}
                    data-testid="onboarding.pasos.paso"
                  >
                    <span className="asistente-marca" aria-hidden="true">
                      {completo ? "✓" : i + 1}
                    </span>
                    <span className="asistente-paso-texto">
                      {t(`asis.${p}.titulo`)}
                      <span className="note-inline">
                        {completo
                          ? t("asis.completo")
                          : OPCIONALES.has(p)
                            ? t("asis.opcional")
                            : t("asis.pendiente")}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <section
          className="asistente-panel"
          aria-labelledby={idTitulo}
          data-testid="onboarding.panel.contenido"
        >
          <p className="note-inline">
            {t("asis.paso", { n: String(indice + 1), total: String(total) })}
            {OPCIONALES.has(paso) && ` · ${t("asis.opcional")}`}
          </p>
          <h2 id={idTitulo} ref={titulo} tabIndex={-1}>
            {t(`asis.${paso}.titulo`)}
          </h2>
          <p>{t(`asis.${paso}.descripcion`)}</p>
          <p data-testid="onboarding.panel.estado">
            <span className={estado.completos[paso] ? "res pass" : "note-inline"}>
              {estado.completos[paso] ? `✓ ${t("asis.completo")}` : t("asis.pendiente")}
            </span>
          </p>
          {(paso === "documentacion" || paso === "activacion") && <CajaError error={grafo.error} />}
          <ContenidoPaso paso={paso} datos={datos} />

          {/* Repositorio y herramientas (KC-PT-07-2) y ramas (KC-PT-07-3) se editan en el paso. */}
          {!PASOS_EN_ASISTENTE.has(paso) && (
            <Link to="/conexiones" data-testid="onboarding.panel.configurar">
              {t("asis.configurar")}
            </Link>
          )}
        </section>

        <div className="asistente-navegacion">
          <button
            type="button"
            className="btn"
            disabled={indice === 0}
            onClick={() => ir(indice - 1)}
            data-testid="onboarding.navegacion.anterior"
          >
            ← {t("asis.anterior")}
          </button>
          {indice < total - 1 && (
            <button
              type="button"
              className="btn primary"
              onClick={() => ir(indice + 1)}
              data-testid="onboarding.navegacion.siguiente"
            >
              {t("asis.siguiente")} →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
