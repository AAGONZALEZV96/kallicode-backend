/**
 * Invitar a una persona por correo (KC-PT-10-5): POST /users/invitations con el correo, el rol
 * (solo los que el usuario puede dar) y un mensaje opcional. El correo trae el código con el que
 * la persona se da de alta en /invitacion (KC-PT-10-6); vence a los 7 días.
 */
import { useId, useState, type FormEvent } from "react";

import { useInvitarUsuario } from "../../api/consultas";
import { atributosDeError, erroresDelServidor, esErrorDeValidacion } from "../../api/erroresCampo";
import { ROLES, type Rol } from "../../api/tipos";
import { ErrorCampo } from "../../componentes/ErrorCampo";
import Modal from "../../componentes/Modal";
import { CajaError } from "../../componentes/ui";
import { useFormato } from "../../estado/formato";
import { useI18n } from "../../estado/i18n";
import { esUnoDe } from "../../tipado";

const MAX_MENSAJE = 500;
// Lo mínimo para no mandar algo que no es un correo; el backend valida el resto (EmailStr).
const PARECE_CORREO = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface Props {
  roles: Rol[];
  alCerrar: () => void;
  alInvitar: (mensaje: string) => void;
}

export default function ModalInvitar({ roles, alCerrar, alInvitar }: Props) {
  const { t } = useI18n();
  const fmt = useFormato();
  const id = useId();
  const invitar = useInvitarUsuario();
  const [email, setEmail] = useState("");
  const [rol, setRol] = useState<Rol>(roles.includes("member") ? "member" : roles[0]!);
  const [mensaje, setMensaje] = useState("");
  const [intentado, setIntentado] = useState(false);

  const propio = PARECE_CORREO.test(email.trim()) ? undefined : t("eq.correo_invalido");
  const servidor = erroresDelServidor(t, invitar.error, (c) => (c === "email" ? "email" : null));
  const errorCorreo = (intentado ? propio : undefined) ?? servidor.porCampo.email;

  function enviar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    if (propio) {
      document.getElementById(`${id}-correo`)?.focus();
      return;
    }
    invitar.mutate(
      { email: email.trim(), rol, mensaje: mensaje.trim() || undefined },
      {
        onSuccess: (inv) =>
          alInvitar(t("eq.invitada", { email: inv.email, fecha: fmt.fechaLarga(inv.expira_en) })),
      },
    );
  }

  const atributos = atributosDeError(`${id}-correo-error`, errorCorreo);
  return (
    <Modal
      titulo={t("eq.invitar")}
      alCerrar={alCerrar}
      bloqueado={invitar.isPending}
      testid="equipo.invitar.dialogo"
    >
      <form className="modal-form" onSubmit={enviar} noValidate>
        <div>
          <label htmlFor={`${id}-correo`}>{t("login.email")}</label>
          <input
            id={`${id}-correo`}
            type="email"
            autoComplete="off"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              invitar.reset();
            }}
            {...atributos}
            data-testid="equipo.invitar.correo"
          />
          <ErrorCampo
            id={`${id}-correo-error`}
            error={errorCorreo}
            testid="equipo.invitar.error-correo"
          />
        </div>
        <div>
          <label htmlFor={`${id}-rol`}>{t("pf.rol")}</label>
          <select
            id={`${id}-rol`}
            value={rol}
            onChange={(e) => {
              if (esUnoDe(ROLES, e.target.value)) setRol(e.target.value);
            }}
            aria-describedby={`${id}-rol-ayuda`}
            data-testid="equipo.invitar.rol"
          >
            {roles.map((r) => (
              <option key={r} value={r}>
                {t(`rol.${r}`)}
              </option>
            ))}
          </select>
          <p id={`${id}-rol-ayuda`} className="ayuda-campo">
            {t(`eq.rol.ayuda.${rol}`)}
          </p>
        </div>
        <div>
          <label htmlFor={`${id}-mensaje`}>{t("eq.mensaje")}</label>
          <textarea
            id={`${id}-mensaje`}
            rows={3}
            maxLength={MAX_MENSAJE}
            value={mensaje}
            onChange={(e) => setMensaje(e.target.value)}
            data-testid="equipo.invitar.mensaje"
          />
        </div>
        {!esErrorDeValidacion(invitar.error) && <CajaError error={invitar.error} />}
        <div className="modal-acciones">
          <button
            type="button"
            className="btn"
            disabled={invitar.isPending}
            onClick={alCerrar}
            data-testid="equipo.invitar.cancelar"
          >
            {t("comun.cancelar")}
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={invitar.isPending}
            data-testid="equipo.invitar.enviar"
          >
            {invitar.isPending ? t("comun.cargando") : t("eq.enviar_invitacion")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
