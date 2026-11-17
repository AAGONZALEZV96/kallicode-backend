/**
 * Alta de una conexión en el asistente (KC-PT-07-2). Los campos salen del catálogo de
 * proveedores (proveedores.ts); las credenciales van a la bóveda del backend y no se
 * vuelven a mostrar. El backend prueba la conexión antes de guardarla: si falla responde
 * CREDENCIALES_INVALIDAS o PERMISOS_EXCESIVOS y no se crea nada.
 * El formulario de bóveda con máscara y rotación llega con KC-PT-07-4.
 */
import { useId, useState, type FormEvent } from "react";

import { useCrearConexion } from "../../api/consultas";
import { atributosDeError } from "../../api/erroresCampo";
import type { ConexionCreada } from "../../api/tipos";
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

interface Props {
  categoria: Categoria;
  alCerrar: () => void;
}

export default function FormularioConexion({ categoria, alCerrar }: Props) {
  const { t } = useI18n();
  const id = useId();
  const opciones = proveedoresDe(categoria);
  const [proveedorId, setProveedorId] = useState(opciones[0]!.id);
  const [nombre, setNombre] = useState("");
  const [valores, setValores] = useState<Valores>({});
  const [intentado, setIntentado] = useState(false);
  const [creada, setCreada] = useState<ConexionCreada | null>(null);
  const crear = useCrearConexion();
  const p = buscarProveedor(proveedorId)!;

  const errores = Object.fromEntries(
    p.campos.map((c) => [c.clave, intentado ? errorDeCampo(c, valores[c.clave] ?? "") : undefined]),
  );
  const idCampo = (clave: string) => `${id}-${clave}`;

  function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    const primero = p.campos.find((c) => errorDeCampo(c, valores[c.clave] ?? ""));
    if (primero) {
      document.getElementById(idCampo(primero.clave))?.focus();
      return;
    }
    crear.mutate(armarCuerpo(p, valores, nombre), {
      onSuccess: (r) => {
        // Las credenciales no se guardan en el navegador más de lo necesario.
        setValores({});
        setCreada(r);
      },
    });
  }

  if (creada) {
    const urlWebhook =
      creada.webhook_token && p.webhook
        ? `${baseApi()}/api/v1/webhooks/${p.webhook}/${creada.webhook_token}`
        : null;
    return (
      <div className="conexion-creada" role="status" data-testid="onboarding.formulario.exito">
        <p>
          ✓ {t("con.creada", { proveedor: t(`con.proveedor.${p.id}`) })}
          {creada.permisos_verificados.length > 0 &&
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
      <div>
        <label htmlFor={`${id}-proveedor`}>{t("con.proveedor")}</label>
        <select
          id={`${id}-proveedor`}
          value={proveedorId}
          onChange={(e) => {
            setProveedorId(e.target.value);
            setValores({});
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
      {p.campos.map((c) => {
        const idError = `${idCampo(c.clave)}-error`;
        // Elemento del data-testid: webhook_secret → webhook-secret.
        const elemento = c.clave.replace(/_/g, "-");
        return (
          <div key={c.clave}>
            <label htmlFor={idCampo(c.clave)}>
              {t(`con.campo.${c.clave}`)}
              {!c.requerido && <span className="note-inline"> · {t("asis.opcional")}</span>}
            </label>
            <input
              id={idCampo(c.clave)}
              type={c.tipo === "secreto" ? "password" : c.tipo === "url" ? "url" : "text"}
              inputMode={c.tipo === "numero" ? "numeric" : undefined}
              autoComplete={c.tipo === "secreto" ? "new-password" : "off"}
              spellCheck={false}
              placeholder={c.ejemplo}
              value={valores[c.clave] ?? ""}
              required={c.requerido}
              onChange={(e) => setValores({ ...valores, [c.clave]: e.target.value })}
              {...atributosDeError(idError, errores[c.clave] && t(`con.error.${errores[c.clave]}`))}
              data-testid={`onboarding.formulario.${elemento}`}
            />
            <ErrorCampo
              id={idError}
              error={errores[c.clave] && t(`con.error.${errores[c.clave]}`)}
              testid="onboarding.formulario.error"
            />
          </div>
        );
      })}
      <CajaError error={crear.error} />
      <div className="conexion-acciones">
        <button
          type="button"
          className="btn"
          onClick={alCerrar}
          disabled={crear.isPending}
          data-testid="onboarding.formulario.cancelar"
        >
          {t("comun.cancelar")}
        </button>
        <button
          type="submit"
          className="btn primary"
          disabled={crear.isPending}
          data-testid="onboarding.formulario.enviar"
        >
          {crear.isPending ? t("con.probando") : t("con.conectar")}
        </button>
      </div>
    </form>
  );
}
