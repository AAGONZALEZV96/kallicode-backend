/**
 * useFormato (KC-PT-09-3): el idioma del portal y la zona horaria de la organización
 * (GET /organization), no los del navegador.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { fijarSesion } from "../api/client";
import { organizacion } from "../mocks/datos";
import { server } from "../mocks/server";
import { Proveedores } from "../test/portal";
import { useFormato } from "./formato";
import { useI18n } from "./i18n";

const ISO = "2026-11-21T17:30:05Z";
const envolver = ({ children }: { children: ReactNode }) => <Proveedores>{children}</Proveedores>;

function zonaDeLaOrganizacion(timezone: string) {
  server.use(
    http.get("*/api/v1/organization", () => HttpResponse.json({ ...organizacion, timezone })),
  );
}

beforeEach(() => fijarSesion("acc_1", "ref_1"));

describe("useFormato", () => {
  it("localiza con la zona horaria de la organización", async () => {
    zonaDeLaOrganizacion("America/Santiago");
    const { result } = renderHook(() => useFormato(), { wrapper: envolver });

    await waitFor(() => expect(result.current.zona).toBe("America/Santiago"));
    expect(result.current.hora(ISO)).toBe("14:30");
  });

  it("si la organización cambia de zona, las fechas siguen la nueva", async () => {
    zonaDeLaOrganizacion("Europe/Madrid");
    const { result } = renderHook(() => useFormato(), { wrapper: envolver });

    await waitFor(() => expect(result.current.hora(ISO)).toBe("18:30"));
  });

  it("usa el idioma elegido en el portal", async () => {
    localStorage.setItem("kc_idioma", "en");
    zonaDeLaOrganizacion("UTC");
    const { result } = renderHook(() => useFormato(), { wrapper: envolver });

    await waitFor(() => expect(result.current.zona).toBe("UTC"));
    expect(result.current.locale).toBe("en");
    expect(result.current.numero(15200)).toBe("15,200");
    expect(result.current.relativo(new Date(Date.now() - 5 * 60_000).toISOString())).toBe(
      "5 minutes ago",
    );
  });

  it("con una zona inválida (o antes de conocerla) no rompe: usa la del navegador", async () => {
    zonaDeLaOrganizacion("Marte/Olympus");
    const { result } = renderHook(() => useFormato(), { wrapper: envolver });

    expect(result.current.zona).toBeUndefined();
    expect(result.current.fecha(ISO)).not.toBe("—");
    await waitFor(() => expect(result.current.fecha(ISO)).not.toBe("—"));
    expect(result.current.zona).toBeUndefined();
  });
});

describe("useFormato al cambiar de idioma", () => {
  it("los formatos cambian en el momento, sin recargar", async () => {
    zonaDeLaOrganizacion("UTC");
    const { result } = renderHook(() => ({ i18n: useI18n(), fmt: useFormato() }), {
      wrapper: envolver,
    });
    expect(result.current.fmt.numero(15200)).toBe("15.200");

    act(() => result.current.i18n.cambiarIdioma("en"));

    expect(result.current.fmt.numero(15200)).toBe("15,200");
    expect(result.current.fmt.locale).toBe("en");
  });
});
