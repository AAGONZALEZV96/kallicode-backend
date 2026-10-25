/** Nueva funcionalidad (§9.3): objetivo + criterios GWT + impacto en vivo (§9.5) y documentos. */
import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { useCrearFuncionalidad, useImpacto } from "../api/consultas";
import type { CriterioGWT } from "../api/tipos";
import SubidaArchivos from "../componentes/SubidaArchivos";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import { useDebounce } from "../useDebounce";
import { useSubidaArchivos } from "../useSubidaArchivos";

export default function NuevaFuncionalidad() {
  const { t } = useI18n();
  const nav = useNavigate();
  
  // Identificadores de accesibilidad (PT-F12)
  const nombreId = useId();
  const objetivoId = useId();

  const [nombre, setNombre] = useState("");
  const [objetivo, setObjetivo] = useState("");
  const [criterios, setCriterios] = useState<CriterioGWT[]>([{ given: "", when: "", then: "" }]);
  const crearFuncionalidad = useCrearFuncionalidad();
  const subida = useSubidaArchivos();

  // Impacto en vivo al dejar de escribir (KC-PT-04-3), igual que la dedup de NuevoTicket.
  // Un fallo no muestra nada.
  const texto = `${nombre} ${objetivo}`.trim();
  const textoConsultado = useDebounce(texto);
  const consulta = texto.length < 10 || textoConsultado.length < 10 ? "" : textoConsultado;
  const { data: impacto } = useImpacto(consulta);

  function setCrit(i: number, campo: keyof CriterioGWT, v: string) {
    setCriterios(criterios.map((c, j) => (j === i ? { ...c, [campo]: v } : c)));
  }

  function enviar(e: FormEvent) {
    e.preventDefault();
    if (subida.subiendo) return;
    const completos = criterios.filter((c) => c.given && c.when && c.then);
    crearFuncionalidad.mutate(
      { nombre, objetivo, criterios: completos, adjuntos: subida.ids },
      { onSuccess: (r) => nav(`/tickets/${r.id}`) },
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
          style={{ display: "flex", flexDirection: "column", gap: 14 }}
        >
          <div>
            <label htmlFor={nombreId}>{t("nf.nombre")}</label>
            <input
              id={nombreId}
              value={nombre}
              required
              minLength={5}
              maxLength={200}
              onChange={(e) => setNombre(e.target.value)}
              data-testid="nueva-funcionalidad.formulario.nombre"
            />
          </div>
          <div>
            <label htmlFor={objetivoId}>{t("nf.objetivo")}</label>
            <textarea
              id={objetivoId}
              rows={3}
              value={objetivo}
              required
              minLength={20}
              onChange={(e) => setObjetivo(e.target.value)}
              data-testid="nueva-funcionalidad.formulario.objetivo"
            />
          </div>
          {/* Los criterios se repiten por fila: se distinguen por posición. */}
          <div role="group" aria-labelledby="lbl-criterios">
            <div id="lbl-criterios" style={{ fontWeight: "bold", marginBottom: "4px" }}>
              {t("nf.criterios")}
            </div>
            {criterios.map((c, i) => (
              <div className="crit-row" key={i} data-testid="nueva-funcionalidad.criterios.fila">
                <input
                  aria-label={`Dado que (Criterio ${i + 1})`}
                  placeholder="Dado que…"
                  value={c.given}
                  onChange={(e) => setCrit(i, "given", e.target.value)}
                  data-testid="nueva-funcionalidad.criterios.dado"
                />
                <input
                  aria-label={`Cuando (Criterio ${i + 1})`}
                  placeholder="Cuando…"
                  value={c.when}
                  onChange={(e) => setCrit(i, "when", e.target.value)}
                  data-testid="nueva-funcionalidad.criterios.cuando"
                />
                <input
                  aria-label={`Entonces (Criterio ${i + 1})`}
                  placeholder="Entonces…"
                  value={c.then}
                  onChange={(e) => setCrit(i, "then", e.target.value)}
                  data-testid="nueva-funcionalidad.criterios.entonces"
                />
                <button
                  type="button"
                  className="btn sm"
                  aria-label={`Quitar criterio ${i + 1}`}
                  onClick={() => setCriterios(criterios.filter((_, j) => j !== i))}
                  data-testid="nueva-funcionalidad.criterios.quitar"
                >
                  ✕
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn sm"
              style={{ marginTop: "8px" }}
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
          <CajaError error={crearFuncionalidad.error} />
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

        <div className="card">
          <div style={{ fontWeight: "bold", marginBottom: "8px" }}>{t("nf.impacto")}</div>
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
        </div>
      </div>
    </div>
  );
}
