/**
 * Comparador de planes y solicitud de mejora (KC-PT-09-1), sobre GET /plans y
 * POST /plans/upgrade-request de KC-BE-09-4.
 *
 * - Muestra los planes de menor a mayor con sus límites, cuál es el vigente y cuánto más (o
 *   menos) trae cada uno respecto de él (el `delta` del backend).
 * - Owner y admin eligen un plan superior al vigente y pueden dejar un comentario; el resto de
 *   los roles solo compara. Antes el botón pedía siempre «enterprise», sin mostrar nada.
 * - Con una solicitud abierta no se ofrece otra: el backend admite una sola por organización y
 *   respondería con la que ya existe.
 */
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { usePlanes, useSolicitarUpgrade } from "../../api/consultas";
import type { LimitesPlan, PlanComparable, SolicitudMejora } from "../../api/tipos";
import { EsqueletoTarjetas } from "../../componentes/Esqueleto";
import Modal from "../../componentes/Modal";
import { CajaError } from "../../componentes/ui";
import { useFormato } from "../../estado/formato";
import { useI18n } from "../../estado/i18n";
import { nivelPlan as nivel, nombrePlan } from "./planes";

const MAX_COMENTARIO = 1000;
const MB = 1024 * 1024;

interface Props {
  /** Owner o admin: puede elegir un plan y enviar la solicitud. */
  puedeSolicitar: boolean;
  /** Solicitud registrada o en gestión: no se ofrece otra. */
  solicitudAbierta: SolicitudMejora | null;
  alCerrar: () => void;
  /** Con el mensaje del backend, para mostrarlo en la vista. */
  alSolicitar: (mensaje: string) => void;
}

export default function ComparadorPlanes({
  puedeSolicitar,
  solicitudAbierta,
  alCerrar,
  alSolicitar,
}: Props) {
  const { t } = useI18n();
  const fmt = useFormato();
  const id = useId();
  const consulta = usePlanes();
  const solicitar = useSolicitarUpgrade();
  const [elegido, setElegido] = useState<string | null>(null);
  const [comentario, setComentario] = useState("");

  const planes = [...(consulta.data?.planes ?? [])].sort(
    (a, b) => nivel(a.codigo) - nivel(b.codigo),
  );
  const vigente = planes.find((p) => p.actual);
  // Solo los conocidos y superiores al vigente: el backend rechaza cualquier otro
  // (UpgradeIn admite starter, growth o enterprise) y, sin plan vigente, toda mejora.
  const superiores = vigente
    ? planes.filter((p) => nivel(p.codigo) !== Infinity && nivel(p.codigo) > nivel(vigente.codigo))
    : [];
  const formulario = puedeSolicitar && !solicitudAbierta && superiores.length > 0;
  // Por defecto, el plan siguiente al vigente.
  const plan = elegido ?? superiores[0]?.codigo ?? null;

  function enviar(e: FormEvent) {
    e.preventDefault();
    if (!plan) return;
    solicitar.mutate(
      { plan, comentario: comentario.trim() || undefined },
      { onSuccess: (r) => alSolicitar(r.mensaje) },
    );
  }

  /** Valor de un límite: «A medida» o «Ilimitadas» cuando el plan no lo fija. */
  function valor(limite: keyof LimitesPlan, n: number | null): string {
    if (n == null) return t(`planes.sin_limite.${limite}`);
    return limite === "storage_mb" ? fmt.bytes(n * MB) : fmt.numero(n);
  }

  /** Cuánto más o menos trae que el vigente: nada si es igual (el vigente) o no se puede comparar. */
  function diferencia(p: PlanComparable, limite: keyof LimitesPlan): ReactNode {
    const d = p.delta[limite];
    if (d == null || d === 0) return null;
    const magnitud =
      limite === "storage_mb" ? fmt.bytes(Math.abs(d) * MB) : fmt.numero(Math.abs(d));
    return (
      <span className={d > 0 ? "plan-delta mas" : "plan-delta menos"}>
        {d > 0 ? "+" : "−"}
        {magnitud}
        <span className="solo-lector"> {t(d > 0 ? "planes.delta.mas" : "planes.delta.menos")}</span>
      </span>
    );
  }

  /** Modalidad de despliegue traducida; una que el portal no conoce, tal cual. */
  function despliegue(d: string): string {
    const clave = `planes.despliegue.${d}`;
    return t(clave) === clave ? d : t(clave);
  }

  function tarjeta(p: PlanComparable) {
    const elegible = formulario && superiores.includes(p);
    const filas: [keyof LimitesPlan, string][] = [
      ["loc_mes", t("planes.loc_mes")],
      ["lineas", t("planes.lineas")],
      ["max_usuarios", t("planes.max_usuarios")],
      ["storage_mb", t("planes.storage_mb")],
    ];
    const idLimites = `${id}-${p.codigo}-limites`;
    const clase = [
      "card",
      "plan-tarjeta",
      p.actual && "actual",
      elegible && plan === p.codigo && "elegido",
    ]
      .filter(Boolean)
      .join(" ");
    return (
      <div
        key={p.codigo}
        className={clase}
        aria-current={p.actual ? "true" : undefined}
        data-testid="consumo.planes.plan"
      >
        <div className="plan-encabezado">
          <h3>{p.nombre}</h3>
          {p.actual && <span className="chip">{t("planes.actual")}</span>}
        </div>
        <dl className="plan-limites" id={idLimites}>
          {filas.map(([limite, etiqueta]) => (
            <div key={limite}>
              <dt>{etiqueta}</dt>
              <dd>
                {valor(limite, p[limite])} {diferencia(p, limite)}
              </dd>
            </div>
          ))}
          <div>
            <dt>{t("planes.despliegues")}</dt>
            <dd>{p.despliegues.map(despliegue).join(", ")}</dd>
          </div>
        </dl>
        {/* Los planes que se pueden pedir son una opción del formulario, con sus límites como descripción. */}
        {elegible && (
          <label className="plan-opcion">
            <input
              type="radio"
              name={`${id}-plan`}
              value={p.codigo}
              checked={plan === p.codigo}
              disabled={solicitar.isPending}
              onChange={() => setElegido(p.codigo)}
              aria-describedby={idLimites}
              data-testid="consumo.planes.opcion"
            />
            {t("planes.elegir", { plan: p.nombre })}
          </label>
        )}
      </div>
    );
  }

  const cerrar = (
    <button
      type="button"
      className="btn"
      disabled={solicitar.isPending}
      onClick={alCerrar}
      data-testid="consumo.planes.cancelar"
    >
      {formulario ? t("comun.cancelar") : t("planes.cerrar")}
    </button>
  );

  let cuerpo: ReactNode;
  if (consulta.error) cuerpo = <CajaError error={consulta.error} />;
  else if (!consulta.data) cuerpo = <EsqueletoTarjetas tarjetas={3} />;
  else if (formulario)
    cuerpo = (
      <form className="modal-form" onSubmit={enviar} noValidate>
        <fieldset className="planes-grid">
          <legend className="solo-lector">{t("planes.leyenda")}</legend>
          {planes.map(tarjeta)}
        </fieldset>
        <div>
          <label htmlFor={`${id}-comentario`}>{t("planes.comentario")}</label>
          <textarea
            id={`${id}-comentario`}
            rows={2}
            maxLength={MAX_COMENTARIO}
            value={comentario}
            disabled={solicitar.isPending}
            onChange={(e) => setComentario(e.target.value)}
            aria-describedby={`${id}-ayuda`}
            data-testid="consumo.planes.comentario"
          />
          <p id={`${id}-ayuda`} className="ayuda-campo">
            {t("planes.comentario.ayuda")}
          </p>
        </div>
        <CajaError error={solicitar.error} />
        <div className="modal-acciones">
          {cerrar}
          <button
            type="submit"
            className="btn primary"
            disabled={!plan || solicitar.isPending}
            data-testid="consumo.planes.enviar"
          >
            {solicitar.isPending ? t("comun.cargando") : t("planes.enviar")}
          </button>
        </div>
      </form>
    );
  else
    cuerpo = (
      <>
        <div className="planes-grid">{planes.map(tarjeta)}</div>
        <p className="note" data-testid="consumo.planes.nota">
          {!puedeSolicitar
            ? t("planes.nota.rol")
            : solicitudAbierta
              ? t("planes.nota.abierta", { plan: nombrePlan(solicitudAbierta.plan_deseado) })
              : vigente
                ? t("planes.nota.maximo")
                : t("planes.nota.sin_plan")}
        </p>
        <div className="modal-acciones">{cerrar}</div>
      </>
    );

  return (
    <Modal
      titulo={t("planes.titulo")}
      alCerrar={alCerrar}
      bloqueado={solicitar.isPending}
      amplio
      testid="consumo.planes.dialogo"
    >
      {cuerpo}
      {/* Si la consulta falla o carga, igual se puede cerrar. */}
      {!consulta.data && <div className="modal-acciones">{cerrar}</div>}
    </Modal>
  );
}
