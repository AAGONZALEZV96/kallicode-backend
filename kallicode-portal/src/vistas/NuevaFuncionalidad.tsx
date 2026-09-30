/** Nueva funcionalidad (§9.3): objetivo + criterios GWT + impacto en vivo (§9.5). */
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

interface Criterio {
  given: string;
  when: string;
  then: string;
}
interface Impacto {
  modulos: string[];
  tablas: string[];
  confianza: number;
}

export default function NuevaFuncionalidad() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [nombre, setNombre] = useState("");
  const [objetivo, setObjetivo] = useState("");
  const [criterios, setCriterios] = useState<Criterio[]>([{ given: "", when: "", then: "" }]);
  const [impacto, setImpacto] = useState<Impacto | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    const texto = `${nombre} ${objetivo}`.trim();
    if (texto.length < 10) {
      setImpacto(null);
      return;
    }
    const timer = setTimeout(() => {
      api
        .post<Impacto>("/api/v1/tickets/impact-preview", { texto })
        .then(setImpacto)
        .catch(() => setImpacto(null));
    }, 600);
    return () => clearTimeout(timer);
  }, [nombre, objetivo]);

  function setCrit(i: number, campo: keyof Criterio, v: string) {
    setCriterios(criterios.map((c, j) => (j === i ? { ...c, [campo]: v } : c)));
  }

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      const completos = criterios.filter((c) => c.given && c.when && c.then);
      const r = await api.post<{ id: string }>("/api/v1/tickets/features", {
        nombre,
        objetivo,
        criterios: completos,
      });
      nav(`/tickets/${r.id}`);
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
          <h1>{t("nf.title")}</h1>
          <p>{t("nf.sub")}</p>
        </div>
      </div>
      <div className="grid2">
        <form
          className="card"
          onSubmit={enviar}
          style={{ display: "flex", flexDirection: "column", gap: 14 }}
        >
          <div>
            <label>{t("nf.nombre")}</label>
            <input
              value={nombre}
              required
              minLength={5}
              maxLength={200}
              onChange={(e) => setNombre(e.target.value)}
            />
          </div>
          <div>
            <label>{t("nf.objetivo")}</label>
            <textarea
              rows={3}
              value={objetivo}
              required
              minLength={20}
              onChange={(e) => setObjetivo(e.target.value)}
            />
          </div>
          <div>
            <label>{t("nf.criterios")}</label>
            {criterios.map((c, i) => (
              <div className="crit-row" key={i}>
                <input
                  placeholder="Dado que…"
                  value={c.given}
                  onChange={(e) => setCrit(i, "given", e.target.value)}
                />
                <input
                  placeholder="Cuando…"
                  value={c.when}
                  onChange={(e) => setCrit(i, "when", e.target.value)}
                />
                <input
                  placeholder="Entonces…"
                  value={c.then}
                  onChange={(e) => setCrit(i, "then", e.target.value)}
                />
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => setCriterios(criterios.filter((_, j) => j !== i))}
                >
                  ✕
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn sm"
              onClick={() => setCriterios([...criterios, { given: "", when: "", then: "" }])}
            >
              {t("nf.addcrit")}
            </button>
          </div>
          <CajaError error={error} />
          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={() => nav("/tickets")}>
              {t("comun.cancelar")}
            </button>
            <button className="btn primary" disabled={enviando}>
              {t("nf.enviar")}
            </button>
          </div>
        </form>

        <div className="card">
          <label>{t("nf.impacto")}</label>
          {!impacto ? (
            <p className="note" style={{ marginTop: 8 }}>
              —
            </p>
          ) : (
            <>
              <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                {impacto.modulos.map((m) => (
                  <span className="chip" key={m}>
                    <span className="dot" />
                    {m}
                  </span>
                ))}
                {impacto.tablas.map((tb) => (
                  <span className="chip" key={tb}>
                    <span className="dot" />
                    {tb} (DB)
                  </span>
                ))}
              </div>
              <p className="note" style={{ marginTop: 8 }}>
                confianza {Math.round(impacto.confianza * 100)}%
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
