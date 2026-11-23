/**
 * Avisos de cuota en todo el portal (KC-PT-09-5): LOC al 80 % o agotada, tickets en cola y
 * almacenamiento de evidencia, desde GET /usage/summary.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { UsageSummary } from "../api/tipos";
import { resumenConsumo, sesion } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

const GB = 1024 ** 3;
/** Intl separa con espacios no separables: se comparan como espacios. */
const texto = (el: HTMLElement) => (el.textContent ?? "").replace(/\s/g, " ");

function resumen(cambios: Partial<UsageSummary>) {
  server.use(
    http.get("*/api/v1/usage/summary", () => HttpResponse.json({ ...resumenConsumo, ...cambios })),
  );
}
const almacenamiento = (umbral: UsageSummary["umbral"], usados: number, porcentaje: number) => ({
  almacenamiento: { usados_bytes: usados, limite_bytes: 25 * GB, porcentaje, umbral },
});
const enCola = { cola: { tickets: 3, desde: "2026-09-20T10:00:00Z" } };

async function enTablero() {
  const usuario = await entrarDesde("/");
  await screen.findByTestId("tablero.vista.raiz");
  return usuario;
}

describe("sin cuotas en riesgo", () => {
  it("con todo normal y sin cola no hay avisos", async () => {
    await entrarDesde("/consumo");
    // Con los KPIs a la vista el resumen ya llegó: los avisos ya tienen sus datos.
    await screen.findByTestId("consumo.kpis.consumidas");

    expect(screen.queryByTestId("layout.avisos.cuota")).toBeNull();
    expect(screen.queryByTestId("layout.avisos.cola")).toBeNull();
    expect(screen.queryByTestId("layout.avisos.almacenamiento")).toBeNull();
  });

  it("si el resumen falla no se muestra nada ni se rompe la vista", async () => {
    server.use(http.get("*/api/v1/usage/summary", () => sobreError(500, "ERROR_INTERNO", "Falló")));
    await enTablero();

    expect(screen.queryByTestId("layout.avisos.cuota")).toBeNull();
    expect(screen.getByTestId("tablero.vista.raiz")).toBeInTheDocument();
  });
});

describe("cuota de LOC", () => {
  it("al 80 %: cuánto se usó, que al agotarse los tickets quedan en cola y hasta cuándo", async () => {
    resumen({ umbral: "aviso_80", loc: { consumidas: 16400, restantes: 3600, porcentaje: 82 } });
    await enTablero();

    const aviso = await screen.findByTestId("layout.avisos.cuota");
    expect(aviso).toHaveClass("aviso-box");
    expect(aviso).toHaveAttribute("role", "status");
    expect(texto(aviso)).toContain(
      "Usaste el 82 % de la cuota de LOC de este ciclo. Si se agota, los tickets nuevos quedarán en cola hasta que se renueve, el 1 oct 2026.",
    );
    expect(within(aviso).getByTestId("layout.avisos.cuota-enlace")).toHaveAttribute(
      "href",
      "/consumo",
    );
  });

  it("el aviso del 80 % se oculta durante la sesión y no vuelve al navegar", async () => {
    resumen({ umbral: "aviso_80" });
    const usuario = await enTablero();

    await usuario.click(await screen.findByRole("button", { name: "Ocultar este aviso" }));
    expect(screen.queryByTestId("layout.avisos.cuota")).toBeNull();
    expect(sessionStorage.getItem("kc_aviso_oculto:loc:2026-09")).toBe("1");

    await usuario.click(screen.getByRole("link", { name: /Tickets/ }));
    await screen.findByTestId("tickets.vista.raiz");
    expect(screen.queryByTestId("layout.avisos.cuota")).toBeNull();
  });

  it("oculto el del 80 %, vuelve a aparecer si la cuota se agota", async () => {
    sessionStorage.setItem("kc_aviso_oculto:loc:2026-09", "1");
    resumen({ umbral: "agotado" });
    await enTablero();

    expect(await screen.findByTestId("layout.avisos.cuota")).toBeInTheDocument();
  });

  it("oculto antes en la sesión (p. ej. antes de recargar), no aparece", async () => {
    sessionStorage.setItem("kc_aviso_oculto:loc:2026-09", "1");
    resumen({ umbral: "aviso_80", ...almacenamiento("aviso_80", 21 * GB, 84) });
    await enTablero();

    // El de almacenamiento sí: su clave es otra.
    await screen.findByTestId("layout.avisos.almacenamiento");
    expect(screen.queryByTestId("layout.avisos.cuota")).toBeNull();
  });

  it("oculto en un ciclo, vuelve en el siguiente", async () => {
    sessionStorage.setItem("kc_aviso_oculto:loc:2026-08", "1");
    resumen({ umbral: "aviso_80" });
    await enTablero();

    expect(await screen.findByTestId("layout.avisos.cuota")).toBeInTheDocument();
  });

  it("agotada: los tickets nuevos quedan en cola, cuántos esperan y el enlace a ellos", async () => {
    resumen({ umbral: "agotado", ...enCola });
    const usuario = await enTablero();

    const aviso = await screen.findByTestId("layout.avisos.cuota");
    expect(aviso).toHaveClass("error-box");
    expect(texto(aviso)).toContain(
      "Se agotó la cuota de LOC de este ciclo: los tickets nuevos quedan en cola hasta que se renueve, el 1 oct 2026. Tickets en cola: 3 (el primero desde el 20 sept 2026).",
    );
    // No se oculta: con la cuota agotada los tickets no avanzan.
    expect(within(aviso).queryByRole("button")).toBeNull();
    // Un solo aviso: la cola va dentro del de la cuota.
    expect(screen.queryByTestId("layout.avisos.cola")).toBeNull();

    await usuario.click(within(aviso).getByTestId("layout.avisos.cuota-enlace"));
    await waitFor(() => expect(rutaActual()).toBe("/tickets?etapa=en_cola_por_cuota"));
    expect(screen.getByTestId("tickets.filtros.etapa")).toHaveValue("en_cola_por_cuota");
    // Ya en esa lista, el enlace no se repite.
    expect(screen.queryByTestId("layout.avisos.cuota-enlace")).toBeNull();
  });

  it("agotada y sin tickets en cola, enlaza a Consumo", async () => {
    resumen({ umbral: "agotado" });
    await enTablero();

    const aviso = await screen.findByTestId("layout.avisos.cuota");
    expect(aviso).not.toHaveTextContent("Tickets en cola");
    expect(within(aviso).getByTestId("layout.avisos.cuota-enlace")).toHaveAttribute(
      "href",
      "/consumo",
    );
  });

  it("sin plan vigente lo dice, sin fecha de renovación", async () => {
    resumen({ umbral: "agotado", plan: null });
    await enTablero();

    expect(await screen.findByTestId("layout.avisos.cuota")).toHaveTextContent(
      "Tu organización no tiene un plan vigente: los tickets nuevos quedan en cola.",
    );
  });

  it("en Consumo no enlaza a Consumo", async () => {
    resumen({ umbral: "aviso_80" });
    await entrarDesde("/consumo");

    expect(await screen.findByTestId("layout.avisos.cuota")).toBeInTheDocument();
    expect(screen.queryByTestId("layout.avisos.cuota-enlace")).toBeNull();
  });
});

describe("tickets en cola con la cuota disponible", () => {
  it("siguen esperando la renovación: su propio aviso, que no se oculta", async () => {
    resumen({ umbral: "normal", ...enCola });
    await enTablero();

    const aviso = await screen.findByTestId("layout.avisos.cola");
    expect(texto(aviso)).toContain(
      "Hay tickets en cola por cuota: 3 (el primero desde el 20 sept 2026). Entrarán al pipeline cuando se renueve la cuota, el 1 oct 2026.",
    );
    expect(within(aviso).queryByRole("button")).toBeNull();
    expect(within(aviso).getByTestId("layout.avisos.cola-enlace")).toHaveAttribute(
      "href",
      "/tickets?etapa=en_cola_por_cuota",
    );
  });

  it("también con la cuota al 80 %, junto a su aviso", async () => {
    resumen({ umbral: "aviso_80", ...enCola });
    await enTablero();

    expect(await screen.findByTestId("layout.avisos.cola")).toBeInTheDocument();
    expect(screen.getByTestId("layout.avisos.cuota")).toBeInTheDocument();
  });
});

describe("almacenamiento de evidencia", () => {
  it("al 80 %: cuánto se usó de cuánto, y se puede ocultar", async () => {
    resumen(almacenamiento("aviso_80", 21 * GB, 84));
    const usuario = await enTablero();

    const aviso = await screen.findByTestId("layout.avisos.almacenamiento");
    expect(aviso).toHaveClass("aviso-box");
    expect(texto(aviso)).toContain(
      "Usaste el 84 % del almacenamiento de evidencia (21 GB de 25 GB). Al llegar al límite no se podrán subir más adjuntos.",
    );
    expect(within(aviso).getByTestId("layout.avisos.almacenamiento-enlace")).toHaveAttribute(
      "href",
      "/consumo",
    );

    await usuario.click(within(aviso).getByTestId("layout.avisos.almacenamiento-ocultar"));
    expect(screen.queryByTestId("layout.avisos.almacenamiento")).toBeNull();
    expect(sessionStorage.getItem("kc_aviso_oculto:almacenamiento:2026-09")).toBe("1");
  });

  it("ocultar el de almacenamiento no oculta el de LOC", async () => {
    resumen({ umbral: "aviso_80", ...almacenamiento("aviso_80", 21 * GB, 84) });
    const usuario = await enTablero();

    await usuario.click(await screen.findByTestId("layout.avisos.almacenamiento-ocultar"));
    expect(screen.getByTestId("layout.avisos.cuota")).toBeInTheDocument();
  });

  it("alcanzado: no se pueden subir más adjuntos y no se oculta", async () => {
    resumen(almacenamiento("agotado", 25 * GB, 100));
    await enTablero();

    const aviso = await screen.findByTestId("layout.avisos.almacenamiento");
    expect(aviso).toHaveClass("error-box");
    expect(texto(aviso)).toContain(
      "Se alcanzó el límite de almacenamiento de evidencia (25 GB): no se pueden subir más adjuntos.",
    );
    expect(within(aviso).queryByRole("button")).toBeNull();
  });

  it("oculto el del 80 %, el de alcanzado se muestra igual", async () => {
    sessionStorage.setItem("kc_aviso_oculto:almacenamiento:2026-09", "1");
    resumen(almacenamiento("agotado", 25 * GB, 100));
    await enTablero();

    expect(await screen.findByTestId("layout.avisos.almacenamiento")).toHaveClass("error-box");
  });

  it("en inglés", async () => {
    localStorage.setItem("kc_idioma", "en");
    resumen(almacenamiento("aviso_80", 21 * GB, 84));
    await enTablero();

    expect(texto(await screen.findByTestId("layout.avisos.almacenamiento"))).toContain(
      "You have used 84% of evidence storage (21 GB of 25 GB).",
    );
  });
});

describe("con la suscripción suspendida", () => {
  it("el aviso se muestra, sin enlace a tickets (solo queda Consumo)", async () => {
    server.use(
      http.post("*/api/v1/auth/login", () =>
        HttpResponse.json({ ...sesion, acceso_restringido: true }),
      ),
      http.get("*/api/v1/users/me", () => sobreError(403, "PERMISO_DENEGADO", "Suspendida")),
    );
    resumen({ umbral: "agotado", ...enCola });
    await entrarDesde("/consumo");

    const aviso = await screen.findByTestId("layout.avisos.cuota");
    expect(aviso).toHaveTextContent("Tickets en cola: 3");
    expect(within(aviso).queryByRole("link")).toBeNull();
  });
});
