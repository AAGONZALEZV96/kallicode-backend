/** Pasos del asistente (KC-PT-07-1): cuándo está hecho cada uno y dónde retomar. */
import { describe, expect, it } from "vitest";

import type { Conexion, EstadoGrafo } from "../../api/tipos";
import {
  estadoAsistente,
  pasoCompleto,
  pasoDeUrl,
  PASOS,
  pasosFaltantes,
  type DatosOnboarding,
} from "./pasos";

const conexion = (categoria: string, estado = "conectada"): Conexion => ({
  id: `cn_${categoria}_${estado}`,
  categoria,
  proveedor: "x",
  nombre: null,
  estado,
  ultimo_test: null,
});

const grafo = (documentos: number): EstadoGrafo => ({
  fase: 4,
  progreso: 0.5,
  metricas: { definiciones: 0, documentos },
  curacion_pendiente: 0,
  ultimo_refresh: null,
});

function datos(cambios: {
  conexiones?: Conexion[];
  ramas?: boolean;
  documentos?: number;
  activa?: boolean;
}): DatosOnboarding {
  return {
    conexiones: {
      conexiones: cambios.conexiones ?? [],
      mapa_ramas: cambios.ramas
        ? { feature: "feature/*", staging: "develop", produccion: "main" }
        : null,
    },
    grafo: cambios.documentos === undefined ? undefined : grafo(cambios.documentos),
    fabricaActiva: cambios.activa ?? false,
  };
}

describe("pasoCompleto", () => {
  it("repositorio: hace falta una conexión de repositorio conectada", () => {
    expect(pasoCompleto("repositorio", datos({ conexiones: [conexion("repositorio")] }))).toBe(
      true,
    );
    expect(
      pasoCompleto("repositorio", datos({ conexiones: [conexion("repositorio", "error")] })),
    ).toBe(false);
    expect(pasoCompleto("repositorio", datos({ conexiones: [conexion("tickets")] }))).toBe(false);
  });

  it("herramientas: basta una de tickets, CI/CD o base de datos conectada", () => {
    for (const categoria of ["tickets", "cicd", "database"]) {
      expect(pasoCompleto("herramientas", datos({ conexiones: [conexion(categoria)] }))).toBe(true);
    }
    expect(pasoCompleto("herramientas", datos({ conexiones: [conexion("repositorio")] }))).toBe(
      false,
    );
  });

  it("ramas, documentación y activación", () => {
    expect(pasoCompleto("ramas", datos({ ramas: true }))).toBe(true);
    expect(pasoCompleto("ramas", datos({}))).toBe(false);
    expect(pasoCompleto("documentacion", datos({ documentos: 2 }))).toBe(true);
    expect(pasoCompleto("documentacion", datos({ documentos: 0 }))).toBe(false);
    // Sin el estado del grafo (falló o no llegó) no se da por hecho.
    expect(pasoCompleto("documentacion", datos({}))).toBe(false);
    expect(pasoCompleto("activacion", datos({ activa: true }))).toBe(true);
  });
});

describe("estadoAsistente: dónde retomar", () => {
  const repo = [conexion("repositorio")];

  it.each([
    ["una organización nueva empieza en el repositorio", datos({}), 0],
    [
      "con repositorio, sigue en el mapa de ramas (salta herramientas, opcional)",
      datos({ conexiones: repo }),
      2,
    ],
    [
      "con repositorio y ramas, sigue en la activación (salta documentación, opcional)",
      datos({ conexiones: repo, ramas: true }),
      4,
    ],
    ["sin repositorio pero con ramas, vuelve al repositorio", datos({ ramas: true }), 0],
    [
      "con todo hecho, queda en la activación",
      datos({ conexiones: repo, ramas: true, activa: true }),
      4,
    ],
  ])("%s", (_caso, d, esperado) => {
    expect(PASOS[estadoAsistente(d).reanudar]).toBe(PASOS[esperado]);
  });

  it("cuenta los pasos hechos, opcionales incluidos", () => {
    const d = datos({
      conexiones: [conexion("repositorio"), conexion("cicd")],
      ramas: true,
      documentos: 1,
      activa: true,
    });

    expect(estadoAsistente(d).cantidadCompletos).toBe(5);
    expect(estadoAsistente(datos({ conexiones: repo })).cantidadCompletos).toBe(1);
  });
});

describe("pasosFaltantes: lo que impide encender la fábrica (KC-PT-07-5)", () => {
  it("sin nada, faltan el repositorio y el mapa de ramas (los opcionales no cuentan)", () => {
    expect(pasosFaltantes(datos({}))).toEqual(["repositorio", "ramas"]);
  });

  it("un repositorio con error no cuenta como conectado", () => {
    expect(
      pasosFaltantes(datos({ conexiones: [conexion("repositorio", "error")], ramas: true })),
    ).toEqual(["repositorio"]);
  });

  it("con repositorio y ramas no falta nada, aunque la fábrica siga apagada", () => {
    expect(pasosFaltantes(datos({ conexiones: [conexion("repositorio")], ramas: true }))).toEqual(
      [],
    );
  });
});

describe("pasoDeUrl", () => {
  it.each([
    ["1", 0],
    ["5", 4],
  ])("?paso=%s → índice %i", (valor, indice) => {
    expect(pasoDeUrl(valor)).toBe(indice);
  });

  it.each([null, "", "0", "6", "-1", "2.5", "dos", "01"])("%j no es un paso", (valor) => {
    expect(pasoDeUrl(valor)).toBeNull();
  });
});
