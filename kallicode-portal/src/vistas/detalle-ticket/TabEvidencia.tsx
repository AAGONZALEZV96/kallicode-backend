/**
 * Pestaña Evidencia QA: resultado por criterio, regresión dirigida y la galería de
 * capturas, videos y trazas (KC-PT-05-2).
 */
import { useState } from "react";

import { usePestanaTicket } from "../../api/consultas";
import { CajaError, Cargando } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import GaleriaEvidencia from "./GaleriaEvidencia";
import SinDatos from "./SinDatos";

export default function TabEvidencia({ numero }: { numero: string }) {
  const { t } = useI18n();
  const {
    data: datos,
    error,
    isPending: cargando,
    refetch,
    dataUpdatedAt,
  } = usePestanaTicket(numero, "evidence");
  // Momento en que se usó un enlace vencido; el aviso dura hasta que llega la respuesta nueva.
  const [vencioEn, setVencioEn] = useState<number | null>(null);
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="evidencia" />;
  return (
    <>
      <table className="kc" data-testid="detalle-ticket.evidencia.tabla">
        <thead>
          <tr>
            <th>ID</th>
            <th>{t("te.resultado")}</th>
            <th>{t("te.detalle")}</th>
          </tr>
        </thead>
        <tbody>
          {datos.matriz.map((m) => (
            <tr key={m.criterio_id}>
              <td>
                <b>{m.criterio_id}</b>
              </td>
              <td>
                <span className={`res ${m.resultado === "pasa" ? "pass" : "fail"}`}>
                  {m.resultado === "pasa"
                    ? `✓ ${t("te.pasa")}`
                    : m.resultado === "falla"
                      ? `✗ ${t("te.falla")}`
                      : `~ ${t("te.flaky")}`}
                </span>
              </td>
              <td className="mono" style={{ fontSize: 11 }}>
                {m.extracto_fallo ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note" style={{ marginTop: 10 }}>
        {t("te.regresion", {
          ok: String(datos.regresion.dirigidos_ok),
          total: String(datos.regresion.dirigidos),
        })}
        {" · "}
        {t("te.suite_cliente", {
          ok: String(datos.regresion.cliente_ok),
          total: String(datos.regresion.suite_cliente),
        })}
      </p>
      {vencioEn !== null && dataUpdatedAt <= vencioEn && (
        <p className="aviso-box" role="status" data-testid="detalle-ticket.evidencia.renovando">
          {t("td.ev.vencido")}
        </p>
      )}
      <GaleriaEvidencia
        matriz={datos.matriz}
        alVencer={() => {
          setVencioEn(Date.now());
          // Varios clics seguidos esperan la misma renovación en vez de reiniciarla.
          void refetch({ cancelRefetch: false });
        }}
      />
    </>
  );
}
