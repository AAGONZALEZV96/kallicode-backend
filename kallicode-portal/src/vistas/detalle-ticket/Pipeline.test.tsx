/** Stepper del pipeline: qué etapa está actual y cuándo se marca el gate que espera. */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TicketDetalle } from "../../api/tipos";
import { detalleTicket } from "../../mocks/datos";
import { Proveedores } from "../../test/portal";
import Pipeline from "./Pipeline";

const ticket = (cambios: Partial<TicketDetalle>): TicketDetalle => ({
  ...detalleTicket("KC-1003")!,
  ...cambios,
});

const etapa = (e: string) => screen.getByTestId(`detalle-ticket.pipeline.etapa-${e}`);

describe("Pipeline", () => {
  it("marca el gate que espera la etapa actual", () => {
    render(
      <Proveedores>
        <Pipeline ticket={ticket({ gate_pendiente: 1 })} />
      </Proveedores>,
    );

    expect(etapa("design")).toHaveAttribute("aria-current", "step");
    expect(within(etapa("design")).getByText("⛨ GATE 1")).toBeInTheDocument();
  });

  it("una etapa sin gate no muestra la bandera aunque no haya gate pendiente (tras firmar)", () => {
    render(
      <Proveedores>
        <Pipeline
          ticket={ticket({
            gate_pendiente: null,
            etapa: "build",
            pipeline: [
              { etapa: "triage", estado: "done", gate: null },
              { etapa: "design", estado: "done", gate: 1 },
              { etapa: "build", estado: "actual", gate: null },
            ],
          })}
        />
      </Proveedores>,
    );

    expect(etapa("build")).toHaveAttribute("aria-current", "step");
    expect(screen.queryByText(/⛨ GATE/)).not.toBeInTheDocument();
  });
});
