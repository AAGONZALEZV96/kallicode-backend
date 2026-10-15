/** Nuevo ticket (§9.2) con deduplicación en vivo (§9.4, RL-2 con debounce). */
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useCrearTicket, useSimilares } from "../api/consultas";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

export default function NuevoTicket() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [tipo, setTipo] = useState<"bug" | "mejora">("bug");
  const [titulo, setTitulo] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [prioridad, setPrioridad] = useState("media");
  const [modulo, setModulo] = useState("");
  const [textoConsultado, setTextoConsultado] = useState("");
  const [aviso, setAviso] = useState("");
  const crearTicket = useCrearTicket();

  // dedup en vivo con debounce de 600 ms (respeta RL-2 del backend); 350 ms en KC-PT-04-3.
  const texto = `${titulo} ${descripcion}`.trim();
  useEffect(() => {
    const timer = setTimeout(() => setTextoConsultado(texto), 600);
    return () => clearTimeout(timer);
  }, [texto]);
  // Bajo 10 caracteres no se consulta (y se limpia al instante, sin esperar el debounce).
  // Un fallo (503 sin embeddings) deja la lista vacía: degradación elegante.
  const consulta = texto.length < 10 || textoConsultado.length < 10 ? "" : textoConsultado;
  const { data: similares = [] } = useSimilares(consulta);

  function enviar(e: FormEvent) {
    e.preventDefault();
    crearTicket.mutate(
      { tipo, titulo, descripcion, prioridad, modulo: modulo || undefined },
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
    <div className="view">
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
          style={{ display: "flex", flexDirection: "column", gap: 14 }}
        >
          <div>
            <label>{t("tk.col.tipo")}</label>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className={`btn sm ${tipo === "bug" ? "primary" : ""}`}
                onClick={() => setTipo("bug")}
              >
                {t("ty.bug")}
              </button>
              <button
                type="button"
                className={`btn sm ${tipo === "mejora" ? "primary" : ""}`}
                onClick={() => setTipo("mejora")}
              >
                {t("ty.mejora")}
              </button>
              <button type="button" className="btn sm" onClick={() => nav("/nueva-funcionalidad")}>
                ✦ {t("ty.funcionalidad")} →
              </button>
            </div>
          </div>
          <div>
            <label>{t("nt.titulo")}</label>
            <input
              value={titulo}
              required
              minLength={5}
              maxLength={200}
              onChange={(e) => setTitulo(e.target.value)}
            />
          </div>
          <div>
            <label>{t("nt.desc")}</label>
            <textarea
              rows={4}
              value={descripcion}
              required
              minLength={10}
              onChange={(e) => setDescripcion(e.target.value)}
            />
          </div>
          <div className="grid2">
            <div>
              <label>{t("nt.prio")}</label>
              <select value={prioridad} onChange={(e) => setPrioridad(e.target.value)}>
                {["alta", "media", "baja"].map((p) => (
                  <option key={p} value={p}>
                    {t(`pr.${p}`)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label>{t("nt.modulo")}</label>
              <input value={modulo} maxLength={200} onChange={(e) => setModulo(e.target.value)} />
            </div>
          </div>
          <CajaError error={crearTicket.error} />
          {aviso && <div className="aviso-box">{aviso}</div>}
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={() => nav("/tickets")}>
              {t("comun.cancelar")}
            </button>
            <button className="btn primary" disabled={crearTicket.isPending}>
              {t("nt.enviar")}
            </button>
          </div>
        </form>

        <div className="card">
          <label>{t("nt.dup")}</label>
          {similares.length === 0 ? (
            <p className="note" style={{ marginTop: 8 }}>
              —
            </p>
          ) : (
            similares.map((s) => (
              <Link
                key={s.ticket_id}
                to={`/tickets/${s.ticket_id}`}
                className="kcard"
                style={{ marginTop: 8 }}
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
