/**
 * Nueva funcionalidad (§9.3): objetivo + criterios GWT + impacto en vivo (§9.5) y
 * documentos. Los errores se muestran junto a cada campo y a cada criterio, los del portal
 * y los del 422 del backend (KC-PT-06-5).
 */
import { Fragment, useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { useCrearFuncionalidad, useImpacto } from "../api/consultas";
import {
  atributosDeError,
  errorDeTexto,
  erroresDelServidor,
  esErrorDeValidacion,
  idDeCampo,
  primeroConError,
  type ErroresCampo,
} from "../api/erroresCampo";
import type { CriterioGWT } from "../api/tipos";
import { ErrorCampo, ResumenErrores } from "../componentes/ErrorCampo";
import QuePasaDespues from "../componentes/QuePasaDespues";
import SubidaArchivos from "../componentes/SubidaArchivos";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import { useDebounce } from "../useDebounce";
import { useSubidaArchivos } from "../useSubidaArchivos";
import { enBlanco, PARTES } from "./nueva-funcionalidad/criterios";
import VistaPreviaSpec from "./nueva-funcionalidad/VistaPreviaSpec";

const ETIQUETA_PARTE = { given: "nf.crit.dado", when: "nf.crit.cuando", then: "nf.crit.entonces" };

/**
 * Campo del backend → clave del formulario. Los criterios del 422 cuentan solo los
 * enviados: `indices` dice en qué fila de la pantalla está cada uno.
 */
const aCampoCon = (indices: number[]) => (campo: string) => {
  if (campo === "nombre" || campo === "objetivo") return campo;
  const m = /^criterios\.(\d+)\.(given|when|then)$/.exec(campo);
  const fila = m ? indices[Number(m[1])] : undefined;
  return m && fila !== undefined ? `criterios.${fila}.${m[2]}` : null;
};

export default function NuevaFuncionalidad() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [nombre, setNombre] = useState("");
  const [objetivo, setObjetivo] = useState("");
  const [criterios, setCriterios] = useState<CriterioGWT[]>([{ given: "", when: "", then: "" }]);
  const crearFuncionalidad = useCrearFuncionalidad();
  const subida = useSubidaArchivos();
  const id = useId();

  // Impacto en vivo al dejar de escribir (KC-PT-04-3), igual que la dedup de NuevoTicket.
  // Un fallo no muestra nada.
  const texto = `${nombre} ${objetivo}`.trim();
  const textoConsultado = useDebounce(texto);
  const consulta = texto.length < 10 || textoConsultado.length < 10 ? "" : textoConsultado;
  const { data: impacto } = useImpacto(consulta);

  // Tras el primer intento de envío, los errores del portal se recalculan al escribir.
  const [intentado, setIntentado] = useState(false);
  // Fila de la pantalla de cada criterio enviado, para ubicar los errores del 422.
  const [enviados, setEnviados] = useState<number[]>([]);
  const campos = [
    "nombre",
    "objetivo",
    ...criterios.flatMap((_, i) => PARTES.map((p) => `criterios.${i}.${p}`)),
  ];
  const locales: ErroresCampo = {
    nombre: errorDeTexto(t, nombre, { min: 5, max: 200 }),
    objetivo: errorDeTexto(t, objetivo, { min: 20, max: 10_000 }),
    ...Object.fromEntries(
      criterios.flatMap((c, i) =>
        enBlanco(c)
          ? []
          : PARTES.map((p) => [`criterios.${i}.${p}`, errorDeTexto(t, c[p], { min: 5, max: 500 })]),
      ),
    ),
  };
  const servidor = erroresDelServidor(t, crearFuncionalidad.error, aCampoCon(enviados));
  const errores: ErroresCampo = Object.fromEntries(
    campos.map((c) => [c, (intentado ? locales[c] : undefined) ?? servidor.porCampo[c]]),
  );
  const hayLocales = intentado && primeroConError(campos, locales) !== undefined;
  const enfocar = (campo: string) => document.getElementById(idDeCampo(id, campo))?.focus();
  const idError = (campo: string) => `${idDeCampo(id, campo)}-error`;

  /** Al corregir algo, el 422 anterior deja de valer: se vuelve a validar al enviar. */
  function alEditar() {
    if (esErrorDeValidacion(crearFuncionalidad.error)) crearFuncionalidad.reset();
  }

  function setCrit(i: number, campo: keyof CriterioGWT, v: string) {
    alEditar();
    setCriterios(criterios.map((c, j) => (j === i ? { ...c, [campo]: v } : c)));
  }

  /** Errores de las tres partes de un criterio, en una sola línea bajo la fila. */
  function errorDeFila(i: number) {
    const partes = PARTES.flatMap((p) => {
      const error = errores[`criterios.${i}.${p}`];
      return error ? [`${t(ETIQUETA_PARTE[p], { n: String(i + 1) })}: ${error}`] : [];
    });
    return partes.length > 0 ? partes.join(" · ") : undefined;
  }

  function enviar(e: FormEvent) {
    e.preventDefault();
    if (subida.subiendo) return;
    setIntentado(true);
    const primero = primeroConError(campos, locales);
    if (primero) {
      enfocar(primero);
      return;
    }
    const indices = criterios.flatMap((c, i) => (enBlanco(c) ? [] : [i]));
    setEnviados(indices);
    crearFuncionalidad.mutate(
      { nombre, objetivo, criterios: indices.map((i) => criterios[i]!), adjuntos: subida.ids },
      {
        onSuccess: (r) => nav(`/tickets/${r.id}`),
        onError: (error) => {
          const porCampo = erroresDelServidor(t, error, aCampoCon(indices)).porCampo;
          const conError = primeroConError(campos, porCampo);
          if (conError) enfocar(conError);
        },
      },
    );
  }

  return (
    <div className="view" data-testid="nueva-funcionalidad.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("nf.title")}</h1>
          <p>{t("nf.sub")}</p>
        </div>
      </div>
      <div className="grid2">
        <form
          className="card"
          onSubmit={enviar}
          onPaste={subida.alPegar}
          // Errores propios junto a cada campo en vez de las burbujas del navegador.
          noValidate
          style={{ display: "flex", flexDirection: "column", gap: 14 }}
        >
          <div>
            <label htmlFor={`${id}-nombre`}>{t("nf.nombre")}</label>
            <input
              id={`${id}-nombre`}
              value={nombre}
              required
              minLength={5}
              maxLength={200}
              onChange={(e) => {
                alEditar();
                setNombre(e.target.value);
              }}
              {...atributosDeError(idError("nombre"), errores.nombre)}
              data-testid="nueva-funcionalidad.formulario.nombre"
            />
            <ErrorCampo
              id={idError("nombre")}
              error={errores.nombre}
              testid="nueva-funcionalidad.errores.nombre"
            />
          </div>
          <div>
            <label htmlFor={`${id}-objetivo`}>{t("nf.objetivo")}</label>
            <textarea
              id={`${id}-objetivo`}
              rows={3}
              value={objetivo}
              required
              minLength={20}
              maxLength={10_000}
              onChange={(e) => {
                alEditar();
                setObjetivo(e.target.value);
              }}
              {...atributosDeError(idError("objetivo"), errores.objetivo)}
              data-testid="nueva-funcionalidad.formulario.objetivo"
            />
            <ErrorCampo
              id={idError("objetivo")}
              error={errores.objetivo}
              testid="nueva-funcionalidad.errores.objetivo"
            />
          </div>
          {/* Los criterios se repiten por fila: se distinguen por posición. Sus campos no
              tienen rótulo visible propio: el nombre accesible lleva el número del criterio. */}
          <div role="group" aria-labelledby={`${id}-criterios`}>
            <p className="caption" id={`${id}-criterios`}>
              {t("nf.criterios")}
            </p>
            {criterios.map((c, i) => {
              const idErrorFila = idError(`criterios.${i}`);
              const atributos = (p: (typeof PARTES)[number]) => ({
                id: idDeCampo(id, `criterios.${i}.${p}`),
                ...atributosDeError(idErrorFila, errores[`criterios.${i}.${p}`]),
              });
              return (
                <Fragment key={i}>
                  <div className="crit-row" data-testid="nueva-funcionalidad.criterios.fila">
                    <input
                      placeholder="Dado que…"
                      aria-label={t("nf.crit.dado", { n: String(i + 1) })}
                      value={c.given}
                      maxLength={500}
                      onChange={(e) => setCrit(i, "given", e.target.value)}
                      {...atributos("given")}
                      data-testid="nueva-funcionalidad.criterios.dado"
                    />
                    <input
                      placeholder="Cuando…"
                      aria-label={t("nf.crit.cuando", { n: String(i + 1) })}
                      value={c.when}
                      maxLength={500}
                      onChange={(e) => setCrit(i, "when", e.target.value)}
                      {...atributos("when")}
                      data-testid="nueva-funcionalidad.criterios.cuando"
                    />
                    <input
                      placeholder="Entonces…"
                      aria-label={t("nf.crit.entonces", { n: String(i + 1) })}
                      value={c.then}
                      maxLength={500}
                      onChange={(e) => setCrit(i, "then", e.target.value)}
                      {...atributos("then")}
                      data-testid="nueva-funcionalidad.criterios.entonces"
                    />
                    <button
                      type="button"
                      className="btn sm"
                      aria-label={t("nf.crit.quitar", { n: String(i + 1) })}
                      onClick={() => {
                        alEditar();
                        setCriterios(criterios.filter((_, j) => j !== i));
                      }}
                      data-testid="nueva-funcionalidad.criterios.quitar"
                    >
                      ✕
                    </button>
                  </div>
                  <ErrorCampo
                    id={idErrorFila}
                    error={errorDeFila(i)}
                    testid="nueva-funcionalidad.criterios.error"
                  />
                </Fragment>
              );
            })}
            <button
              type="button"
              className="btn sm"
              onClick={() => setCriterios([...criterios, { given: "", when: "", then: "" }])}
              data-testid="nueva-funcionalidad.criterios.agregar"
            >
              {t("nf.addcrit")}
            </button>
          </div>
          <SubidaArchivos
            subida={subida}
            etiqueta={t("nf.docs")}
            ayuda={t("nf.drop")}
            vista="nueva-funcionalidad"
          />
          {hayLocales || esErrorDeValidacion(crearFuncionalidad.error) ? (
            <ResumenErrores
              mensaje={t("form.revisar")}
              sueltos={servidor.sueltos}
              testid="nueva-funcionalidad.errores.resumen"
            />
          ) : (
            <CajaError error={crearFuncionalidad.error} />
          )}
          {subida.subiendo && (
            <p className="note" role="status">
              {t("adj.esperando")}
            </p>
          )}
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <button
              type="button"
              className="btn"
              onClick={() => nav("/tickets")}
              data-testid="nueva-funcionalidad.formulario.cancelar"
            >
              {t("comun.cancelar")}
            </button>
            <button
              className="btn primary"
              disabled={crearFuncionalidad.isPending || subida.subiendo}
              data-testid="nueva-funcionalidad.formulario.enviar"
            >
              {t("nf.enviar")}
            </button>
          </div>
        </form>

        <div className="columna-lateral">
          <section className="card" aria-labelledby={`${id}-impacto`} aria-live="polite">
            <p className="caption" id={`${id}-impacto`}>
              {t("nf.impacto")}
            </p>
            {!impacto ? (
              <p
                className="note"
                style={{ marginTop: 8 }}
                data-testid="nueva-funcionalidad.impacto.vacio"
              >
                —
              </p>
            ) : (
              <>
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  {impacto.modulos.map((m) => (
                    <span className="chip" key={m} data-testid="nueva-funcionalidad.impacto.modulo">
                      <span className="dot" />
                      {m}
                    </span>
                  ))}
                  {impacto.tablas.map((tb) => (
                    <span className="chip" key={tb} data-testid="nueva-funcionalidad.impacto.tabla">
                      <span className="dot" />
                      {tb} (DB)
                    </span>
                  ))}
                </div>
                <p
                  className="note"
                  style={{ marginTop: 8 }}
                  data-testid="nueva-funcionalidad.impacto.confianza"
                >
                  confianza {Math.round(impacto.confianza * 100)}%
                </p>
              </>
            )}
          </section>
          <VistaPreviaSpec nombre={nombre} objetivo={objetivo} criterios={criterios} />
          <QuePasaDespues vista="nueva-funcionalidad" tipo="funcionalidad" />
        </div>
      </div>
    </div>
  );
}
