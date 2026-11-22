/**
 * Visor de los cambios de código del expediente (KC-PT-08-1, sobre KC-BE-08-6).
 *
 * - Un archivo por cambio: su nombre con las líneas agregadas y eliminadas, el racional técnico
 *   y el enlace al PR. Al abrirlo, el diff unificado con la numeración de ambas versiones.
 * - El SHA-256 del diff se recalcula en el navegador y se compara con el que registró el
 *   backend: si no coincide, lo dice. Un diff cortado por largo no se puede verificar aquí.
 * - El diff viene del repositorio del cliente: se muestra como texto, nunca como HTML.
 */
import { useEffect, useId, useMemo, useState } from "react";

import { parsearDiff, verificarDiff, type LineaDiff, type Verificacion } from "../../api/diff";
import type { Expediente } from "../../api/tipos";
import { useI18n } from "../../estado/i18n";
import { sanearUrl } from "../../urls";

type Cambio = Expediente["cambios"][number];
type EstadoSello = Verificacion | "verificando" | "cortado";

/** Tope del backend (servicios.expediente.MAX_CARACTERES_DIFF): para el aviso de diff cortado. */
const MAX_CARACTERES_DIFF = 200_000;

const SIGNO: Partial<Record<LineaDiff["tipo"], string>> = {
  agregada: "+",
  eliminada: "-",
  contexto: " ",
};

export default function VisorDiff({ cambios }: { cambios: Cambio[] }) {
  const { t } = useI18n();
  if (cambios.length === 0) {
    return (
      <p className="note" data-testid="expediente.cambios.vacio">
        {t("au.sin_cambios")}
      </p>
    );
  }
  return (
    <ul className="cambios">
      {cambios.map((c) => (
        // Un solo archivo se muestra abierto; con varios, el usuario elige cuál.
        <ArchivoCambiado key={c.referencia} cambio={c} abiertoAlInicio={cambios.length === 1} />
      ))}
    </ul>
  );
}

function useSello(cambio: Cambio): EstadoSello {
  const [estado, setEstado] = useState<EstadoSello>(
    cambio.diff_truncado ? "cortado" : "verificando",
  );
  useEffect(() => {
    if (cambio.diff_truncado) return;
    let vigente = true;
    verificarDiff(cambio.diff, cambio.hash_sha256).then(
      (v) => vigente && setEstado(v),
      () => vigente && setEstado("no-disponible"),
    );
    return () => {
      vigente = false;
    };
  }, [cambio.diff, cambio.hash_sha256, cambio.diff_truncado]);
  return estado;
}

function ArchivoCambiado({
  cambio,
  abiertoAlInicio,
}: {
  cambio: Cambio;
  abiertoAlInicio: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  const [abierto, setAbierto] = useState(abiertoAlInicio);
  const diff = useMemo(() => parsearDiff(cambio.diff), [cambio.diff]);
  const sello = useSello(cambio);
  const pr = sanearUrl(cambio.pr_url);
  const conteo = t("au.diff.conteo", {
    agregadas: String(diff.agregadas),
    eliminadas: String(diff.eliminadas),
  });

  return (
    <li className="cambio">
      <div className="cambio-cabecera">
        <button
          type="button"
          className="cambio-archivo"
          aria-expanded={abierto}
          aria-controls={abierto ? `${id}-diff` : undefined}
          onClick={() => setAbierto((v) => !v)}
          data-testid="expediente.cambios.archivo"
        >
          <span aria-hidden="true">{abierto ? "▾" : "▸"}</span>
          <span className="mono">{cambio.archivo}</span>
          <span className="diff-conteo" aria-hidden="true">
            <span className="diff-mas">+{diff.agregadas}</span>{" "}
            <span className="diff-menos">−{diff.eliminadas}</span>
          </span>
          <span className="solo-lector">{conteo}</span>
        </button>
        {pr && (
          <a
            href={pr}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="expediente.cambios.pr"
          >
            {t("au.ver_pr")} ↗
          </a>
        )}
      </div>
      <p className="note">{cambio.racional}</p>
      <p
        className={`diff-sello ${sello}`}
        title={`SHA-256 ${cambio.hash_sha256}`}
        data-testid="expediente.cambios.verificacion"
      >
        {t(`au.diff.sello.${sello}`)}
      </p>
      {cambio.diff_truncado && (
        <p className="note-inline">
          {t("au.diff.truncado", { n: MAX_CARACTERES_DIFF.toLocaleString() })}
        </p>
      )}
      {abierto && (
        <div className="diff-marco" id={`${id}-diff`}>
          <table
            className="diff"
            aria-label={t("au.diff.tabla", { archivo: cambio.archivo })}
            data-testid="expediente.cambios.diff"
          >
            <thead className="solo-lector">
              <tr>
                <th scope="col">{t("au.diff.col.antes")}</th>
                <th scope="col">{t("au.diff.col.despues")}</th>
                <th scope="col">{t("au.diff.col.codigo")}</th>
              </tr>
            </thead>
            <tbody>
              {diff.lineas.map((l, i) => (
                <tr key={i} className={`diff-${l.tipo}`}>
                  <td className="diff-num">{l.antes ?? ""}</td>
                  <td className="diff-num">{l.despues ?? ""}</td>
                  <td className="diff-codigo">
                    {SIGNO[l.tipo] ?? ""}
                    {l.texto}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </li>
  );
}
