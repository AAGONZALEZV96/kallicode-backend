/**
 * Los tipos generados siguen al contrato congelado (KC-PT-04-2): si alguien cambia
 * contratos/api-v1.yaml y no regenera con `npm run tipos:api`, esta prueba falla.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";
import { describe, expect, it } from "vitest";

// Vitest corre desde kallicode-portal/, como el script tipos:api.
const leer = (ruta: string) => readFileSync(resolve(ruta), "utf-8").replace(/\r\n/g, "\n");

describe("tipos generados desde OpenAPI", () => {
  it("api/generado/api-v1.ts está al día con contratos/api-v1.yaml", async () => {
    // Mismas opciones que el script tipos:api.
    const ast = await openapiTS(leer("../contratos/api-v1.yaml"), { defaultNonNullable: false });

    expect(leer("src/api/generado/api-v1.ts")).toBe(COMMENT_HEADER + astToString(ast));
  }, 30_000);
});
