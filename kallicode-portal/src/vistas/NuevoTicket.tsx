/** Nuevo ticket (§9.2) con deduplicación en vivo (§9.4, RL-2 con debounce). */
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

interface Similar {
  ticket_id: string;
  titulo: string;
  similitud: number;
  etapa: string;
  resuelto_en: string | null;
}

export default function NuevoTicket() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [tipo, setTipo] = useState<"bug" | "mejora">("bug");
  const [titulo, setTitulo] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [prioridad, setPrioridad] = useState("media");
  const [modulo, setModulo] = useState("");
  const [similares, setSimilares] = useState<Similar[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState("");

  // dedup en vivo con debounce de 600 ms (respeta RL-2 del backend)
  useEffect(() => {
    const texto = `${titulo} ${descripcion}`.trim();
    if (texto.length < 10) {
      setSimilares([]);
      return;
    }
    const timer = setTimeout(() => {
      api
        .post<{ similares: Similar[] }>("/api/v1/tickets/dedup-preview", { texto, k: 3 })
        .then((r) => setSimilares(r.similares))
        .catch(() => setSimilares([])); // degradación elegante (503)
    }, 600);
    return () => clearTimeout(timer);
  }, [titulo, descripcion]);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      const r = await api.post<{ id: string; etapa: string; aviso?: string }>("/api/v1/tickets", {
        tipo,
        titulo,
        descripcion,
        prioridad,
        modulo: modulo || undefined,
      });
      if (r.etapa === "en_cola_por_cuota") {
        setAviso("Cuota mensual agotada: el ticket quedó en cola (QU-1).");
        setTimeout(() => nav(`/tickets/${r.id}`), 1500);
      } else {
        nav(`/tickets/${r.id}`);
      }
    } catch (err) {
      setError(err);
    } finally {
      setEnviando(false);
    }
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
          <CajaError error={error} />
          {aviso && <div className="aviso-box">{aviso}</div>}
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={() => nav("/tickets")}>
              {t("comun.cancelar")}
            </button>
            <button className="btn primary" disabled={enviando}>
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
