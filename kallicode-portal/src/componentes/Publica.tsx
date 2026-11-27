/**
 * Marco de las pantallas públicas (KC-PT-10-6): la tarjeta centrada del login con el logo y el
 * título de la pantalla. Recuperar y restablecer la contraseña y aceptar una invitación se
 * abren sin sesión, desde el correo.
 */
import type { ReactNode } from "react";

import { useI18n } from "../estado/i18n";

export default function Publica({
  titulo,
  testid,
  children,
}: {
  titulo: string;
  testid: string;
  children: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <main className="login-wrap" data-testid={testid}>
      <div className="card login-card publica">
        <div className="logo mb-18">
          <div className="mark">&lt;×&gt;</div>
          <div>
            kallicode<small>{t("login.titulo")}</small>
          </div>
        </div>
        <h1 className="publica-titulo">{titulo}</h1>
        {children}
      </div>
    </main>
  );
}
