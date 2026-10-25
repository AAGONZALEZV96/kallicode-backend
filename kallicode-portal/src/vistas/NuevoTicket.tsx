/** Nuevo ticket (§9.2) con deduplicación en vivo (§9.4, RL-2 con debounce) y evidencia adjunta. */
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useCrearTicket, useSimilares } from "../api/consultas";
import { PrioridadEsquema, type Prioridad } from "../api/tipos";
import SubidaArchivos from "../componentes/SubidaArchivos";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import { useDebounce } from "../useDebounce";
import { useSubidaArchivos } from "../useSubidaArchivos";

export default function NuevoTicket() {
  const { t } = useI18n();
  const nav = useNavigate();
  
  // Identificadores de accesibilidad (PT-F12)
  const tipoId = useId();
  const tituloId = useId();
  const descId = useId();
  const prioId = useId();
  const moduloId = useId();

  const [tipo, setTipo] = useState<"bug" | "mejora">("bug");
  const [titulo, setTitulo] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [prioridad, setPrioridad] = useState<Prioridad>("media");
  const [modulo, setModulo] = useState("");
  const [aviso, setAviso] = useState("");
  const crearTicket = useCrearTicket();
  // Cada archivo se sube al agregarlo; el ticket se crea con los que ya terminaron.
  const subida = useSubidaArchivos();

  // dedup en vivo al dejar de escribir (KC-PT-04-3): una petición por pausa, no por tecla,
  // dentro del límite RL-2 del backend (30/min por usuario).
  const texto = `${titulo} ${descripcion}`.trim();
  const textoConsultado = useDebounce(texto);
  // Bajo 10 caracteres no se consulta (y se limpia al instante, sin esperar el debounce).
  // Un fallo (503 sin embeddings) deja la lista vacía: degradación elegante.
  const consulta = texto.length < 10 || textoConsultado.length < 10 ? "" : textoConsultado;
  const { data: similares = [] } = useSimilares(consulta);

  function enviar(e: FormEvent) {
    e.preventDefault();
    if (subida.subiendo) return;
    crearTicket.mutate(
      { tipo, titulo, descripcion, prioridad, modulo: modulo || undefined, adjuntos: subida.ids },
      {
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
          style={{ display: "flex", flexDirection: "column", gap: 14 }}
        >
          <div>
            {/* Convertido a div/group para no dejar un label huérfano de input (PT-F12) */}
            <div id={tipoId} style={{ fontWeight: "bold", marginBottom: "4px" }}>
              {t("tk.col.tipo")}
            </div>
            <div role="group" aria-labelledby={tipoId} style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className={`btn sm ${tipo === "bug" ? "primary" : ""}`}
                onClick={() => setTipo("bug")}
                data-testid="nuevo-ticket.formulario.tipo-bug"
              >
                {t("ty.bug")}
              </button>
              <button
                type="button"
                className={`btn sm ${tipo === "mejora" ? "primary" : ""}`}
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
            <label htmlFor={tituloId}>{t("nt.titulo")}</label>
            <input
              id={tituloId}
              value={titulo}
              required
              minLength={5}
              maxLength={200}
              onChange={(e) => setTitulo(e.target.value)}
              data-testid="nuevo-ticket.formulario.titulo"
            />
          </div>
          <div>
            <label htmlFor={descId}>{t("nt.desc")}</label>
            <textarea
              id={descId}
              rows={4}
              value={descripcion}
              required
              minLength={10}
              onChange={(e) => setDescripcion(e.target.value)}
              data-testid="nuevo-ticket.formulario.descripcion"
            />
          </div>
          <div className="grid2">
            <div>
              <label htmlFor={prioId}>{t("nt.prio")}</label>
              <select
                id={prioId}
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
              <label htmlFor={moduloId}>{t("nt.modulo")}</label>
              <input
                id={moduloId}
                value={modulo}
                maxLength={200}
                onChange={(e) => setModulo(e.target.value)}
                data-testid="nuevo-ticket.formulario.modulo"
              />
            </div>
          </div>
          <SubidaArchivos
            subida={subida}
            etiqueta={t("nt.evidencia")}
            ayuda={t("nt.drop")}
            vista="nuevo-ticket"
          />
          <CajaError error={crearTicket.error} />
          {aviso && (
            <div className="aviso-box" role="status" data-testid="nuevo-ticket.formulario.aviso">
              {aviso}
            </div>
          )}
          {subida.subiendo && (
            <p className="note" role="status" aria-live="polite">
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

        <div className="card">
          {/* Reemplazado <label> huérfano por <div> semántico para Axe */}
          <div style={{ fontWeight: "bold", marginBottom: "8px" }}>{t("nt.dup")}</div>
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
        </div>
      </div>
    </div>
  );
}
