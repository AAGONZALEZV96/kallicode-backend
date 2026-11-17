/**
 * Formulario de bóveda de una conexión: alta (KC-PT-07-2) y rotación de credenciales
 * (KC-PT-07-4). Los campos salen del catálogo de proveedores (proveedores.ts).
 *
 * Seguridad de las credenciales:
 * - van a la bóveda del backend y nunca se vuelven a mostrar: al rotar, los campos secretos
 *   llegan vacíos con una máscara («••••••••») que indica que hay algo guardado;
 * - lo que se escribe va enmascarado (password) y cada secreto tiene un botón para verlo
 *   un momento (aria-pressed); al guardar o al cambiar de proveedor se vacían y se ocultan;
 * - el backend prueba la conexión antes de guardar: si falla responde
 *   CREDENCIALES_INVALIDAS o PERMISOS_EXCESIVOS y no cambia nada.
 * Al rotar se piden todos los datos del proveedor: GET /connections no devuelve la config,
 * y así `repo_url` y `db_url` se vuelven a armar completas (armarCuerpo).
 */
import { useId, useState, type FormEvent } from "react";

import { useCrearConexion, useRotarCredenciales } from "../../api/consultas";
import { atributosDeError } from "../../api/erroresCampo";
import type { Conexion, ConexionCreada } from "../../api/tipos";
import { ErrorCampo } from "../../componentes/ErrorCampo";
import { CajaError } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import {
  armarCuerpo,
  errorDeCampo,
  proveedor as buscarProveedor,
  proveedoresDe,
  type Categoria,
  type Valores,
} from "./proveedores";

/** Base de la API para mostrar la URL del webhook que hay que configurar en la herramienta. */
const baseApi = () => import.meta.env.VITE_API_URL || window.location.origin;

type Props = { alCerrar: () => void } & (
  { categoria: Categoria; rotar?: undefined } | { rotar: Conexion; categoria?: undefined }
);

/** Resultado que se muestra al terminar: la conexión creada o la rotación hecha. */
type Hecho = { tipo: "creada"; conexion: ConexionCreada } | { tipo: "rotada" };

export default function FormularioConexion({ categoria, rotar, alCerrar }: Props) {
  const { t } = useI18n();
  const id = useId();
  const opciones = rotar ? [] : proveedoresDe(categoria);
  const [proveedorId, setProveedorId] = useState(rotar ? rotar.proveedor : opciones[0]!.id);
  const [nombre, setNombre] = useState("");
  const [valores, setValores] = useState<Valores>({});
  const [visibles, setVisibles] = useState<ReadonlySet<string>>(new Set());
  const [intentado, setIntentado] = useState(false);
  const [hecho, setHecho] = useState<Hecho | null>(null);
  const crear = useCrearConexion();
  const rotacion = useRotarCredenciales();
  const envio = rotar ? rotacion : crear;
  const p = buscarProveedor(proveedorId);

  if (!p) {
    // Una conexión de un proveedor que el portal no conoce no se puede rotar desde aquí.
    return (
      <div className="conexion-formulario">
        <p className="note-inline">{t("con.rotar_sin_catalogo")}</p>
        <button type="button" className="btn sm" onClick={alCerrar}>
          {t("con.listo")}
        </button>
      </div>
    );
  }

  const nombreProveedor = t(`con.proveedor.${p.id}`);
  const errores = Object.fromEntries(
    p.campos.map((c) => [c.clave, intentado ? errorDeCampo(c, valores[c.clave] ?? "") : undefined]),
  );
  const idCampo = (clave: string) => `${id}-${clave}`;

  /** Las credenciales no se guardan en el navegador más de lo necesario. */
  function olvidarCredenciales() {
    setValores({});
    setVisibles(new Set());
  }

  function alternarVisible(clave: string) {
    const siguientes = new Set(visibles);
    if (siguientes.has(clave)) siguientes.delete(clave);
    else siguientes.add(clave);
    setVisibles(siguientes);
  }

  function enviar(e: FormEvent) {
    e.preventDefault();
    if (!p) return;
    setIntentado(true);
    const primero = p.campos.find((c) => errorDeCampo(c, valores[c.clave] ?? ""));
    if (primero) {
      document.getElementById(idCampo(primero.clave))?.focus();
      return;
    }
    const cuerpo = armarCuerpo(p, valores, nombre);
    if (rotar) {
      rotacion.mutate(
        { id: rotar.id, config: cuerpo.config, credenciales: cuerpo.credenciales },
        {
          onSuccess: () => {
            olvidarCredenciales();
            setHecho({ tipo: "rotada" });
          },
        },
      );
    } else {
      crear.mutate(cuerpo, {
        onSuccess: (r) => {
          olvidarCredenciales();
          setHecho({ tipo: "creada", conexion: r });
        },
      });
    }
  }

  if (hecho) {
    const creada = hecho.tipo === "creada" ? hecho.conexion : null;
    const urlWebhook =
      creada?.webhook_token && p.webhook
        ? `${baseApi()}/api/v1/webhooks/${p.webhook}/${creada.webhook_token}`
        : null;
    return (
      <div className="conexion-creada" role="status" data-testid="onboarding.formulario.exito">
        <p>
          ✓{" "}
          {creada
            ? t("con.creada", { proveedor: nombreProveedor })
            : t("con.rotada", { proveedor: nombreProveedor })}
          {creada &&
            creada.permisos_verificados.length > 0 &&
            ` ${t("con.permisos", { permisos: creada.permisos_verificados.join(", ") })}`}
        </p>
        {urlWebhook && (
          <div>
            <label htmlFor={`${id}-webhook`}>{t("con.webhook")}</label>
            <input
              id={`${id}-webhook`}
              value={urlWebhook}
              readOnly
              className="mono"
              onFocus={(e) => e.currentTarget.select()}
              data-testid="onboarding.formulario.webhook"
            />
            <p className="ayuda-campo">{t("con.webhook_ayuda")}</p>
          </div>
        )}
        <button type="button" className="btn sm" onClick={alCerrar}>
          {t("con.listo")}
        </button>
      </div>
    );
  }

  return (
    <form className="conexion-formulario" onSubmit={enviar} noValidate>
      {rotar ? (
        <p className="ayuda-campo">{t("con.rotar_ayuda", { proveedor: nombreProveedor })}</p>
      ) : (
        <>
          <div>
            <label htmlFor={`${id}-proveedor`}>{t("con.proveedor")}</label>
            <select
              id={`${id}-proveedor`}
              value={proveedorId}
              onChange={(e) => {
                setProveedorId(e.target.value);
                olvidarCredenciales();
                setIntentado(false);
                crear.reset();
              }}
              data-testid="onboarding.formulario.proveedor"
            >
              {opciones.map((o) => (
                <option key={o.id} value={o.id}>
                  {t(`con.proveedor.${o.id}`)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={`${id}-nombre`}>
              {t("con.nombre")}
              <span className="note-inline"> · {t("asis.opcional")}</span>
            </label>
            <input
              id={`${id}-nombre`}
              value={nombre}
              maxLength={120}
              onChange={(e) => setNombre(e.target.value)}
              data-testid="onboarding.formulario.nombre"
            />
          </div>
        </>
      )}
      {p.campos.map((c) => {
        const idError = `${idCampo(c.clave)}-error`;
        // Elemento del data-testid: webhook_secret → webhook-secret.
        const elemento = c.clave.replace(/_/g, "-");
        const secreto = c.tipo === "secreto";
        const visible = visibles.has(c.clave);
        const etiqueta = t(`con.campo.${c.clave}`);
        return (
          <div key={c.clave}>
            <label htmlFor={idCampo(c.clave)}>
              {etiqueta}
              {!c.requerido && <span className="note-inline"> · {t("asis.opcional")}</span>}
            </label>
            <div className={secreto ? "campo-secreto" : undefined}>
              <input
                id={idCampo(c.clave)}
                type={secreto && !visible ? "password" : c.tipo === "url" ? "url" : "text"}
                inputMode={c.tipo === "numero" ? "numeric" : undefined}
                autoComplete={secreto ? "new-password" : "off"}
                spellCheck={false}
                // Al rotar, la máscara indica que hay un secreto guardado (que no se muestra).
                placeholder={secreto && rotar ? t("con.secreto_guardado") : c.ejemplo}
                value={valores[c.clave] ?? ""}
                required={c.requerido}
                onChange={(e) => setValores({ ...valores, [c.clave]: e.target.value })}
                {...atributosDeError(
                  idError,
                  errores[c.clave] && t(`con.error.${errores[c.clave]}`),
                )}
                data-testid={`onboarding.formulario.${elemento}`}
              />
              {secreto && (
                <button
                  type="button"
                  className="btn sm"
                  aria-pressed={visible}
                  aria-controls={idCampo(c.clave)}
                  aria-label={t("con.mostrar_a", { campo: etiqueta })}
                  onClick={() => alternarVisible(c.clave)}
                  data-testid="onboarding.formulario.mostrar"
                >
                  {/* Texto fijo: el estado lo da aria-pressed (y el estilo del botón). */}
                  {t("con.mostrar")}
                </button>
              )}
            </div>
            <ErrorCampo
              id={idError}
              error={errores[c.clave] && t(`con.error.${errores[c.clave]}`)}
              testid="onboarding.formulario.error"
            />
          </div>
        );
      })}
      <CajaError error={envio.error} />
      <div className="conexion-acciones">
        <button
          type="button"
          className="btn"
          onClick={alCerrar}
          disabled={envio.isPending}
          data-testid="onboarding.formulario.cancelar"
        >
          {t("comun.cancelar")}
        </button>
        <button
          type="submit"
          className="btn primary"
          disabled={envio.isPending}
          data-testid="onboarding.formulario.enviar"
        >
          {envio.isPending ? t("con.probando") : rotar ? t("con.rotar_enviar") : t("con.conectar")}
        </button>
      </div>
    </form>
  );
}
