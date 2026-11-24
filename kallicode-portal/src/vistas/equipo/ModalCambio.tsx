/**
 * Confirmar un cambio de rol o de estado de otra persona (KC-PT-10-5), PATCH /users/{id}.
 * Dice qué va a pasar (desactivar cierra sus sesiones; reactivar ocupa un lugar del plan) y deja
 * en el diálogo los errores del backend: último propietario, único arquitecto, cupo del plan.
 */
import { useActualizarUsuario } from "../../api/consultas";
import type { Rol, UsuarioOrg } from "../../api/tipos";
import Modal from "../../componentes/Modal";
import { CajaError } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";

export interface CambioUsuario {
  usuario: UsuarioOrg;
  cambios: { rol: Rol } | { estado: string };
}

interface Props {
  cambio: CambioUsuario;
  alCerrar: () => void;
  alCambiar: (mensaje: string) => void;
}

export default function ModalCambio({ cambio, alCerrar, alCambiar }: Props) {
  const { t } = useI18n();
  const actualizar = useActualizarUsuario();
  const { usuario, cambios } = cambio;
  const nombre = usuario.nombre;

  const [titulo, explicacion, listo] =
    "rol" in cambios
      ? [
          t("eq.cambio.rol", { nombre }),
          t("eq.cambio.rol.texto", {
            nombre,
            antes: t(`rol.${usuario.rol}`),
            despues: t(`rol.${cambios.rol}`),
          }),
          t("eq.cambio.rol.listo", { nombre, despues: t(`rol.${cambios.rol}`) }),
        ]
      : cambios.estado === "desactivado"
        ? [
            t("eq.desactivar_a", { nombre }),
            t("eq.cambio.desactivar.texto", { nombre }),
            t("eq.cambio.desactivar.listo", { nombre }),
          ]
        : [
            t("eq.reactivar_a", { nombre }),
            t("eq.cambio.reactivar.texto", { nombre }),
            t("eq.cambio.reactivar.listo", { nombre }),
          ];

  function confirmar() {
    actualizar.mutate({ id: usuario.id, cambios }, { onSuccess: () => alCambiar(listo) });
  }

  return (
    <Modal
      titulo={titulo}
      alCerrar={alCerrar}
      bloqueado={actualizar.isPending}
      testid="equipo.cambio.dialogo"
    >
      <p>{explicacion}</p>
      <CajaError error={actualizar.error} />
      <div className="modal-acciones">
        <button
          type="button"
          className="btn"
          disabled={actualizar.isPending}
          onClick={alCerrar}
          data-testid="equipo.cambio.cancelar"
        >
          {t("comun.cancelar")}
        </button>
        <button
          type="button"
          className={
            "estado" in cambios && cambios.estado === "desactivado" ? "btn warn" : "btn primary"
          }
          disabled={actualizar.isPending}
          onClick={confirmar}
          data-testid="equipo.cambio.confirmar"
        >
          {actualizar.isPending ? t("comun.cargando") : t("eq.confirmar")}
        </button>
      </div>
    </Modal>
  );
}
