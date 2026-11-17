/**
 * Paso 3 del asistente (KC-PT-07-3): editar y guardar el mapa de ramas con
 * PUT /connections/branch-map. Sin mapa se proponen los valores por defecto del backend,
 * y el paso queda completo al guardarlos.
 * Errores del backend: VALIDACION_ENTRADA (422, staging igual a producción o largo) junto
 * al campo; JOBS_ACTIVOS (409, hay Build o Deploy en curso) en la caja de error.
 */
import { useId, useState, type FormEvent } from "react";

import { useGuardarMapaRamas } from "../../api/consultas";
import {
  atributosDeError,
  erroresDelServidor,
  esErrorDeValidacion,
  primeroConError,
} from "../../api/erroresCampo";
import { ErrorCampo } from "../../componentes/ErrorCampo";
import { CajaError } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import {
  CAMPOS_RAMA,
  LARGO_MAXIMO,
  MAPA_SUGERIDO,
  errorDeRama,
  limpiar,
  esCampoRama,
  type CampoRama,
  type MapaRamas,
} from "./ramas";

export default function MapaRamasPaso({ mapa }: { mapa: MapaRamas | null }) {
  const { t } = useI18n();
  const id = useId();
  const guardar = useGuardarMapaRamas();
  const [valores, setValores] = useState<MapaRamas>(mapa ?? MAPA_SUGERIDO);
  const [intentado, setIntentado] = useState(false);

  const idCampo = (campo: CampoRama) => `${id}-${campo}`;
  const mensaje = (campo: CampoRama) => {
    const error = errorDeRama(campo, valores);
    if (!error) return undefined;
    if (error === "requerido") return t("campo.requerido");
    if (error === "max") return t("campo.max", { n: String(LARGO_MAXIMO) });
    return t(`ramas.error.${error}`);
  };
  // El 422 sin detalle de campos es el de staging igual a producción.
  const delServidor = (error: unknown) =>
    erroresDelServidor(t, error, (c) => (esCampoRama(c) ? c : null), "produccion");
  const servidor = delServidor(guardar.error);
  const errores = Object.fromEntries(
    CAMPOS_RAMA.map((c) => [c, (intentado ? mensaje(c) : undefined) ?? servidor.porCampo[c]]),
  );

  function cambiar(campo: CampoRama, valor: string) {
    // Al editar, el resultado anterior (guardado o error del servidor) deja de valer.
    if (guardar.isSuccess || guardar.isError) guardar.reset();
    setValores({ ...valores, [campo]: valor });
  }

  function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    const primero = CAMPOS_RAMA.find((c) => errorDeRama(c, valores));
    if (primero) {
      document.getElementById(idCampo(primero))?.focus();
      return;
    }
    const limpio = limpiar(valores);
    guardar.mutate(limpio, {
      // Los campos quedan como se guardaron (sin espacios alrededor).
      onSuccess: () => setValores(limpio),
      onError: (err) => {
        if (!esErrorDeValidacion(err)) return;
        const campo = primeroConError(CAMPOS_RAMA, delServidor(err).porCampo);
        if (campo && esCampoRama(campo)) document.getElementById(idCampo(campo))?.focus();
      },
    });
  }

  return (
    <div className="ramas-paso">
      {mapa ? (
        <p className="mono" data-testid="onboarding.resumen.ramas">
          {mapa.feature} → {mapa.staging} → {mapa.produccion}
        </p>
      ) : (
        <p className="note-inline" data-testid="onboarding.resumen.vacio">
          {t("asis.sin_ramas")} {t("ramas.sugerido")}
        </p>
      )}

      <form className="ramas-formulario" onSubmit={enviar} noValidate>
        <div className="grid3">
          {CAMPOS_RAMA.map((campo) => {
            const idError = `${idCampo(campo)}-error`;
            const idAyuda = `${idCampo(campo)}-ayuda`;
            const atributos = atributosDeError(idError, errores[campo]);
            return (
              <div key={campo}>
                <label htmlFor={idCampo(campo)}>{t(`ramas.${campo}`)}</label>
                <input
                  id={idCampo(campo)}
                  className="mono"
                  value={valores[campo]}
                  maxLength={LARGO_MAXIMO}
                  required
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(e) => cambiar(campo, e.target.value)}
                  aria-invalid={atributos["aria-invalid"]}
                  // La ayuda siempre; el error, cuando lo hay.
                  aria-describedby={[idAyuda, atributos["aria-describedby"]]
                    .filter(Boolean)
                    .join(" ")}
                  data-testid={`onboarding.ramas.${campo}`}
                />
                <p id={idAyuda} className="ayuda-campo">
                  {t(`ramas.ayuda_${campo}`)}
                </p>
                <ErrorCampo id={idError} error={errores[campo]} testid="onboarding.ramas.error" />
              </div>
            );
          })}
        </div>
        {!esErrorDeValidacion(guardar.error) && <CajaError error={guardar.error} />}
        <div className="conexion-acciones">
          <button
            type="submit"
            className="btn primary"
            disabled={guardar.isPending}
            data-testid="onboarding.ramas.guardar"
          >
            {guardar.isPending ? t("ramas.guardando") : t("ramas.guardar")}
          </button>
          {/* Región viva siempre presente: se anuncia cuando cambia su texto. */}
          <span
            role="status"
            className={guardar.isSuccess ? "res pass" : undefined}
            data-testid="onboarding.ramas.exito"
          >
            {guardar.isSuccess && `✓ ${t("ramas.guardado")}`}
          </span>
        </div>
      </form>
    </div>
  );
}
