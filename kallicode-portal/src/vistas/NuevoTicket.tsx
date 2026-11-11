/**
 * Nuevo ticket (§9.2) con deduplicación en vivo (§9.4, RL-2 con debounce) y evidencia
 * adjunta. Los errores se muestran junto a cada campo, los del portal y los del 422 del
 * backend (KC-PT-06-5).
 */
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useCrearTicket, useSimilares } from "../api/consultas";
import {
  atributosDeError,
  errorDeTexto,
  erroresDelServidor,
  esErrorDeValidacion,
  idDeCampo,
  primeroConError,
  type ErroresCampo,
} from "../api/erroresCampo";
import { PrioridadEsquema, type Prioridad } from "../api/tipos";
import { ErrorCampo, ResumenErrores } from "../componentes/ErrorCampo";
import SubidaArchivos from "../componentes/SubidaArchivos";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import { useDebounce } from "../useDebounce";
import { useSubidaArchivos } from "../useSubidaArchivos";

/** Campos con error propio, en el orden del formulario (TicketIn del backend). */
const CAMPOS = ["titulo", "descripcion", "modulo"] as const;
const aCampo = (campo: string) => ((CAMPOS as readonly string[]).includes(campo) ? campo : null);

export default function NuevoTicket() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [tipo, setTipo] = useState<"bug" | "mejora">("bug");
  const [titulo, setTitulo] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [prioridad, setPrioridad] = useState<Prioridad>("media");
  const [modulo, setModulo] = useState("");
  const [aviso, setAviso] = useState("");
  const crearTicket = useCrearTicket();
  // Cada archivo se sube al agregarlo; el ticket se crea con los que ya terminaron.
  const subida = useSubidaArchivos();
  const id = useId();

  // dedup en vivo al dejar de escribir (KC-PT-04-3): una petición por pausa, no por tecla,
  // dentro del límite RL-2 del backend (30/min por usuario).
  const texto = `${titulo} ${descripcion}`.trim();
  const textoConsultado = useDebounce(texto);
  // Bajo 10 caracteres no se consulta (y se limpia al instante, sin esperar el debounce).
  // Un fallo (503 sin embeddings) deja la lista vacía: degradación elegante.
  const consulta = texto.length < 10 || textoConsultado.length < 10 ? "" : textoConsultado;
  const { data: similares = [] } = useSimilares(consulta);

  // Tras el primer intento de envío, los errores del portal se recalculan al escribir.
  const [intentado, setIntentado] = useState(false);
  const locales: ErroresCampo = {
    titulo: errorDeTexto(t, titulo, { min: 5, max: 200 }),
    descripcion: errorDeTexto(t, descripcion, { min: 10, max: 10_000 }),
    modulo: errorDeTexto(t, modulo, { max: 200 }),
  };
  const servidor = erroresDelServidor(t, crearTicket.error, aCampo);
  const errores: ErroresCampo = Object.fromEntries(
    CAMPOS.map((c) => [c, (intentado ? locales[c] : undefined) ?? servidor.porCampo[c]]),
  );
  const hayLocales = intentado && primeroConError(CAMPOS, locales) !== undefined;
  const enfocar = (campo: string) => document.getElementById(idDeCampo(id, campo))?.focus();
  const idError = (campo: string) => `${idDeCampo(id, campo)}-error`;

  /** Al corregir un campo, el 422 anterior deja de valer: se vuelve a validar al enviar. */
  function editar<T>(asignar: (v: T) => void) {
    return (valor: T) => {
      if (esErrorDeValidacion(crearTicket.error)) crearTicket.reset();
      asignar(valor);
    };
  }

  function enviar(e: FormEvent) {
    e.preventDefault();
    if (subida.subiendo) return;
    setIntentado(true);
    const primero = primeroConError(CAMPOS, locales);
    if (primero) {
      enfocar(primero);
      return;
    }
    crearTicket.mutate(
      { tipo, titulo, descripcion, prioridad, modulo: modulo || undefined, adjuntos: subida.ids },
      {
        onError: (error) => {
          const conError = primeroConError(CAMPOS, erroresDelServidor(t, error, aCampo).porCampo);
          if (conError) enfocar(conError);
        },
        onSuccess: (r) => {
          if (r.etapa === "en_cola_por_cuota") {
            setAviso("Cuota mensual agotada: el ticket quedó en cola (QU-1).");
            setTimeout(() => nav(`/tickets/${r.id}`), 1500);
          } else {
            nav(`/tickets/${r.id}`);
          }
        },
      },
    );
  }

  return (
    <div className="view" data-testid="nuevo-ticket.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("nt.title")}</h1>
          <p>{t("nt.sub")}</p>
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
            {/* Rótulo de un grupo de botones, no de un campo: caption + aria-labelledby. */}
            <p className="caption" id={`${id}-tipo`}>
              {t("tk.col.tipo")}
            </p>
            <div role="group" aria-labelledby={`${id}-tipo`} style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className={`btn sm ${tipo === "bug" ? "primary" : ""}`}
                aria-pressed={tipo === "bug"}
                onClick={() => setTipo("bug")}
                data-testid="nuevo-ticket.formulario.tipo-bug"
              >
                {t("ty.bug")}
              </button>
              <button
                type="button"
                className={`btn sm ${tipo === "mejora" ? "primary" : ""}`}
                aria-pressed={tipo === "mejora"}
                onClick={() => setTipo("mejora")}
                data-testid="nuevo-ticket.formulario.tipo-mejora"
              >
                {t("ty.mejora")}
              </button>
              <button
                type="button"
                className="btn sm"
                onClick={() => nav("/nueva-funcionalidad")}
                data-testid="nuevo-ticket.formulario.tipo-funcionalidad"
              >
                ✦ {t("ty.funcionalidad")} →
              </button>
            </div>
          </div>
          <div>
            <label htmlFor={`${id}-titulo`}>{t("nt.titulo")}</label>
            <input
              id={`${id}-titulo`}
              value={titulo}
              required
              minLength={5}
              maxLength={200}
              onChange={(e) => editar(setTitulo)(e.target.value)}
              {...atributosDeError(idError("titulo"), errores.titulo)}
              data-testid="nuevo-ticket.formulario.titulo"
            />
            <ErrorCampo
              id={idError("titulo")}
              error={errores.titulo}
              testid="nuevo-ticket.errores.titulo"
            />
          </div>
          <div>
            <label htmlFor={`${id}-descripcion`}>{t("nt.desc")}</label>
            <textarea
              id={`${id}-descripcion`}
              rows={4}
              value={descripcion}
              required
              minLength={10}
              maxLength={10_000}
              onChange={(e) => editar(setDescripcion)(e.target.value)}
              {...atributosDeError(idError("descripcion"), errores.descripcion)}
              data-testid="nuevo-ticket.formulario.descripcion"
            />
            <ErrorCampo
              id={idError("descripcion")}
              error={errores.descripcion}
              testid="nuevo-ticket.errores.descripcion"
            />
          </div>
          <div className="grid2">
            <div>
              <label htmlFor={`${id}-prioridad`}>{t("nt.prio")}</label>
              <select
                id={`${id}-prioridad`}
                value={prioridad}
                onChange={(e) => {
                  const elegida = PrioridadEsquema.safeParse(e.target.value);
                  if (elegida.success) setPrioridad(elegida.data);
                }}
                data-testid="nuevo-ticket.formulario.prioridad"
              >
                {["alta", "media", "baja"].map((p) => (
                  <option key={p} value={p}>
                    {t(`pr.${p}`)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={`${id}-modulo`}>{t("nt.modulo")}</label>
              <input
                id={`${id}-modulo`}
                value={modulo}
                maxLength={200}
                onChange={(e) => editar(setModulo)(e.target.value)}
                {...atributosDeError(idError("modulo"), errores.modulo)}
                data-testid="nuevo-ticket.formulario.modulo"
              />
              <ErrorCampo
                id={idError("modulo")}
                error={errores.modulo}
                testid="nuevo-ticket.errores.modulo"
              />
            </div>
          </div>
          <SubidaArchivos
            subida={subida}
            etiqueta={t("nt.evidencia")}
            ayuda={t("nt.drop")}
            vista="nuevo-ticket"
          />
          {hayLocales || esErrorDeValidacion(crearTicket.error) ? (
            <ResumenErrores
              mensaje={t("form.revisar")}
              sueltos={servidor.sueltos}
              testid="nuevo-ticket.errores.resumen"
            />
          ) : (
            <CajaError error={crearTicket.error} />
          )}
          {aviso && (
            <div className="aviso-box" role="status" data-testid="nuevo-ticket.formulario.aviso">
              {aviso}
            </div>
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
              data-testid="nuevo-ticket.formulario.cancelar"
            >
              {t("comun.cancelar")}
            </button>
            <button
              className="btn primary"
              disabled={crearTicket.isPending || subida.subiendo}
              data-testid="nuevo-ticket.formulario.enviar"
            >
              {t("nt.enviar")}
            </button>
          </div>
        </form>

        <section className="card" aria-labelledby={`${id}-duplicados`} aria-live="polite">
          <p className="caption" id={`${id}-duplicados`}>
            {t("nt.dup")}
          </p>
          {similares.length === 0 ? (
            <p className="note" style={{ marginTop: 8 }} data-testid="nuevo-ticket.similares.vacio">
              —
            </p>
          ) : (
            similares.map((s) => (
              <Link
                key={s.ticket_id}
                to={`/tickets/${s.ticket_id}`}
                className="kcard"
                style={{ marginTop: 8 }}
                data-testid="nuevo-ticket.similares.tarjeta"
              >
                <span className="kid">
                  {s.ticket_id} · {Math.round(Number(s.similitud) * 100)}%
                </span>
                <br />
                {s.titulo}
              </Link>
            ))
          )}
        </section>
      </div>
    </div>
  );
}
