/** Panel «¿Qué pasa después de enviar?» (KC-PT-06-6): los pasos del pipeline y sus gates. */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Proveedores } from "../test/portal";
import QuePasaDespues from "./QuePasaDespues";

function abrir(tipo: "ticket" | "funcionalidad") {
  render(
    <Proveedores>
      <QuePasaDespues vista="nuevo-ticket" tipo={tipo} />
    </Proveedores>,
  );
  return screen.getByRole("region", { name: "¿Qué pasa después de enviar?" });
}

const pasos = () => screen.getAllByTestId("nuevo-ticket.que-pasa.paso");

describe("QuePasaDespues", () => {
  it("lista los pasos del pipeline en el orden del orquestador", () => {
    const panel = abrir("ticket");

    expect(within(panel).getByRole("list")).toBe(pasos()[0]!.parentElement);
    expect(pasos().map((p) => p.querySelector("b")?.textContent)).toEqual([
      "Triage",
      "Diseño / Spec",
      "GATE 1 · Aprobar diseño",
      "Build",
      "QA / Testing",
      "GATE 2 · Validar evidencia",
      "Security",
      "GATE 3 · Firma de arquitecto",
      "En producción",
    ]);
  });

  it("marca los tres gates como firma humana, con quién puede firmar", () => {
    abrir("ticket");

    const conFirma = pasos().filter((p) => within(p).queryByText("Firma humana"));
    expect(conFirma.map((p) => p.querySelector("b")?.textContent)).toEqual([
      "GATE 1 · Aprobar diseño",
      "GATE 2 · Validar evidencia",
      "GATE 3 · Firma de arquitecto",
    ]);
    expect(conFirma[0]).toHaveTextContent("owner, admin, arquitecto o aprobador");
    expect(conFirma[2]).toHaveTextContent("Solo un arquitecto");
  });

  it("explica los retrocesos (QA y seguridad vuelven a build), los duplicados y la cuota", () => {
    const panel = abrir("ticket");

    expect(pasos()[0]).toHaveTextContent("Si es un duplicado, se cierra.");
    expect(pasos()[4]).toHaveTextContent("Si algo falla, vuelve a build.");
    expect(pasos()[6]).toHaveTextContent("Un hallazgo grave lo devuelve a build.");
    expect(panel).toHaveTextContent("el ticket queda en cola hasta que haya cupo");
  });

  it("para una funcionalidad, el diseño parte del objetivo y los criterios", () => {
    abrir("funcionalidad");

    expect(pasos()[1]).toHaveTextContent("convierte tu objetivo y tus criterios en una spec");
  });
});
