/** Pestaña Spec: alternativas evaluadas, criterios Given/When/Then y alcance autorizado. */
import { usePestanaTicket } from "../../api/consultas";
import { CajaError, Cargando } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import SinDatos from "./SinDatos";

export default function TabSpec({ numero }: { numero: string }) {
  const { t } = useI18n();
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "spec");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="spec" />;
  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 12 }}
      data-testid="detalle-ticket.spec.contenido"
    >
      <p style={{ fontSize: 12, color: "var(--muted)" }}>
        Spec v{datos.version} · {datos.estado}
      </p>
      <div>
        <b>{t("td.alt")}</b>
        <div className="grid2" style={{ marginTop: 6 }}>
          {datos.alternativas.map((a) => (
            <div
              key={a.id}
              className="card"
              style={
                a.elegida || a.recomendada
                  ? { borderWidth: 2, borderColor: "var(--accent)" }
                  : undefined
              }
            >
              <b>
                {a.id} · {a.titulo}
              </b>
              <p style={{ fontSize: 12, color: "var(--muted)" }}>
                {a.descripcion} {a.recomendada && "✔"}
              </p>
            </div>
          ))}
        </div>
      </div>
      <div>
        <b>{t("td.criterios")}</b>
        <table className="kc" style={{ marginTop: 6 }}>
          <tbody>
            {datos.criterios.map((c) => (
              <tr key={c.id}>
                <td style={{ width: 60 }}>
                  <b>{c.id}</b>
                </td>
                <td>
                  Dado {c.given}, cuando {c.when}, entonces {c.then}.
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <b>{t("td.scope")}</b>
        <p className="note" style={{ marginTop: 6 }}>
          <span className="mono">{datos.scope_paths.join(" · ")}</span>
        </p>
      </div>
    </div>
  );
}
