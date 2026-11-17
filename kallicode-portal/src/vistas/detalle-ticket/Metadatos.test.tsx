/**
 * Metadatos del ticket (KC-PT-06-3): origen con su nombre y radio de impacto del triage.
 * La ETA queda fuera: el backend siempre la envía en null.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { TicketDetalleEsquema, type TicketDetalle } from "../../api/tipos";
import { detalleTicket } from "../../mocks/datos";
import { entrarDesde, Proveedores } from "../../test/portal";
import Cabecera from "./Cabecera";
import Resumen from "./Resumen";

const base = detalleTicket("KC-1003")!;

function ticket(cambios: {
  origen?: string;
  radio_impacto?: TicketDetalle["detalle"]["radio_impacto"];
}): TicketDetalle {
  return {
    ...base,
    ...(cambios.origen ? { origen: cambios.origen } : {}),
    detalle: {
      ...base.detalle,
      ...("radio_impacto" in cambios ? { radio_impacto: cambios.radio_impacto } : {}),
    },
  };
}

function resumen(t: TicketDetalle) {
  render(
    <Proveedores>
      <Resumen ticket={t} />
    </Proveedores>,
  );
}

const id = (elemento: string) => screen.getByTestId(`detalle-ticket.resumen.${elemento}`);
const quiza = (elemento: string) => screen.queryByTestId(`detalle-ticket.resumen.${elemento}`);
/** Valor de la fila cuya etiqueta es `nombre`. */
const fila = (nombre: string) => screen.getByRole("row", { name: new RegExp(`^${nombre}`) });

describe("radio de impacto", () => {
  it("muestra el radio, el riesgo de regresión y los módulos que dejó el triage", () => {
    resumen(
      ticket({
        radio_impacto: {
          modulos: ["billing", "core.facturas"],
          radio: "medium",
          riesgo_regresion: 0.35,
        },
      }),
    );

    expect(id("impacto-radio")).toHaveTextContent("Medio");
    expect(id("impacto-radio")).toHaveClass("prio", "media");
    expect(id("impacto-riesgo").textContent).toBe("35 %");
    expect(
      within(id("impacto-modulos"))
        .getAllByRole("listitem")
        .map((m) => m.textContent),
    ).toEqual(["billing", "core.facturas"]);
    expect(quiza("impacto-pendiente")).not.toBeInTheDocument();
  });

  it.each([
    ["high", "Alto", "alta"],
    ["low", "Bajo", "baja"],
  ] as const)("radio %s → %s, con el color de la prioridad %s", (radio, texto, clase) => {
    resumen(ticket({ radio_impacto: { modulos: [], radio, riesgo_regresion: null } }));

    expect(id("impacto-radio")).toHaveTextContent(texto);
    expect(id("impacto-radio")).toHaveClass(clase);
  });

  it("antes del triage dice que el impacto está pendiente", () => {
    resumen(ticket({ radio_impacto: null }));

    expect(id("impacto-pendiente")).toHaveTextContent("Pendiente de triage");
    expect(quiza("impacto-radio")).not.toBeInTheDocument();
  });

  it("si al impacto le faltan partes, cada una queda en «—»", () => {
    resumen(ticket({ radio_impacto: { modulos: [], radio: null, riesgo_regresion: null } }));

    expect(fila("Radio de impacto")).toHaveTextContent("—");
    expect(fila("Riesgo de regresión")).toHaveTextContent("—");
    expect(fila("Módulos afectados")).toHaveTextContent("—");
    expect(quiza("impacto-pendiente")).not.toBeInTheDocument();
  });

  it("no muestra ETA (el backend no la calcula)", () => {
    resumen(ticket({}));

    expect(screen.queryByText(/\bETA\b|Estimaci[oó]n/)).not.toBeInTheDocument();
  });
});

describe("esquema tolerante (el contrato declara el impacto como objeto libre)", () => {
  const conImpacto = (radio_impacto: unknown) =>
    TicketDetalleEsquema.parse({
      ...base,
      detalle: { ...base.detalle, radio_impacto, estimacion: null },
    }).detalle;

  it("acepta el impacto del triage e ignora el razonamiento en inglés y la estimación", () => {
    const detalle = conImpacto({
      modulos: ["billing"],
      radio: "high",
      riesgo_regresion: 0.8,
      razonamiento: "Touches the invoicing core.",
    });

    expect(detalle.radio_impacto).toEqual({
      modulos: ["billing"],
      radio: "high",
      riesgo_regresion: 0.8,
    });
    expect(detalle).not.toHaveProperty("estimacion");
  });

  it("descarta lo que no tiene la forma esperada en vez de romper el detalle", () => {
    expect(conImpacto({ modulos: "billing", radio: "extremo", riesgo_regresion: 3 })).toEqual(
      expect.objectContaining({
        radio_impacto: { modulos: [], radio: null, riesgo_regresion: null },
      }),
    );
    expect(conImpacto("alto").radio_impacto).toBeNull();
    expect(conImpacto(undefined).radio_impacto).toBeUndefined();
  });
});

describe("origen", () => {
  const cabecera = (origen: string) => {
    render(
      <Proveedores>
        <MemoryRouter>
          <Cabecera ticket={ticket({ origen })} />
        </MemoryRouter>
      </Proveedores>,
    );
    return screen.getByTestId("detalle-ticket.cabecera.origen");
  };

  it.each([
    ["portal", "Portal"],
    ["jira", "Jira"],
    ["github", "GitHub"],
    ["kallicode_help", "Kallicode Help"],
    ["corestream", "CoreStream"],
  ])("%s → %s", (origen, nombre) => {
    expect(cabecera(origen).textContent).toBe(nombre);
  });

  it("un origen que el portal no conoce se muestra tal cual", () => {
    expect(cabecera("slack").textContent).toBe("slack");
  });
});

describe("en el detalle completo", () => {
  it("un ticket con triage muestra su impacto y su origen", async () => {
    await entrarDesde("/tickets/KC-1003");
    expect(await screen.findByTestId("detalle-ticket.resumen.impacto-radio")).toHaveTextContent(
      "Medio",
    );
    expect(screen.getByTestId("detalle-ticket.cabecera.origen")).toHaveTextContent("Portal");
  });

  it("en triage el impacto está pendiente", async () => {
    await entrarDesde("/tickets/KC-1001");
    expect(
      await screen.findByTestId("detalle-ticket.resumen.impacto-pendiente"),
    ).toBeInTheDocument();
  });
});
