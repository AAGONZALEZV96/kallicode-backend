/** Modal accesible (KC-PT-06-7): foco, Escape, fondo y bloqueo mientras se envía. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import Modal from "./Modal";

function Demo({ bloqueado = false }: { bloqueado?: boolean }) {
  const [abierto, setAbierto] = useState(false);
  return (
    <>
      <button onClick={() => setAbierto(true)}>Abrir</button>
      {abierto && (
        <Modal titulo="Confirmar" alCerrar={() => setAbierto(false)} bloqueado={bloqueado}>
          <input aria-label="Motivo" />
          <button onClick={() => setAbierto(false)}>Listo</button>
        </Modal>
      )}
    </>
  );
}

async function abrir(bloqueado = false) {
  const usuario = userEvent.setup();
  render(<Demo bloqueado={bloqueado} />);
  await usuario.click(screen.getByRole("button", { name: "Abrir" }));
  return usuario;
}

describe("Modal", () => {
  it("es un diálogo modal con nombre y el foco entra al primer control", async () => {
    await abrir();

    const dialogo = screen.getByRole("dialog", { name: "Confirmar" });
    expect(dialogo).toHaveAttribute("aria-modal", "true");
    expect(screen.getByLabelText("Motivo")).toHaveFocus();
  });

  it("Tab queda atrapado dentro del diálogo en ambos sentidos", async () => {
    const usuario = await abrir();

    await usuario.tab();
    expect(screen.getByRole("button", { name: "Listo" })).toHaveFocus();
    await usuario.tab();
    expect(screen.getByLabelText("Motivo")).toHaveFocus();
    await usuario.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Listo" })).toHaveFocus();
  });

  it("Escape cierra y devuelve el foco a quien lo abrió", async () => {
    const usuario = await abrir();

    await usuario.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Abrir" })).toHaveFocus();
  });

  it("un clic en el fondo cierra; dentro del diálogo no", async () => {
    const usuario = await abrir();

    await usuario.click(screen.getByRole("heading", { name: "Confirmar" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await usuario.click(screen.getByRole("dialog").parentElement!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("bloqueado (enviando) no se cierra con Escape ni con el fondo", async () => {
    const usuario = await abrir(true);

    await usuario.keyboard("{Escape}");
    await usuario.click(screen.getByRole("dialog").parentElement!);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
