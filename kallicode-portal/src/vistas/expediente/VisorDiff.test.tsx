/**
 * Visor de diffs del expediente (KC-PT-08-1), en el portal completo: archivos con su racional y
 * su PR, el diff unificado con numeración y la verificación del sello SHA-256 en el navegador.
 */
import { createHash } from "node:crypto";

import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Expediente } from "../../api/tipos";
import { expediente } from "../../mocks/datos";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

type Cambio = Expediente["cambios"][number];
const sha = (texto: string) => createHash("sha256").update(texto, "utf8").digest("hex");

function cambio(archivo: string, diff: string, extra: Partial<Cambio> = {}): Cambio {
  return {
    archivo,
    pr_url: "https://github.com/demo/app/pull/479",
    racional: `Racional de ${archivo}`,
    referencia: `trazas-llm/org_demo/KC-1001/st_${archivo}/salida.json`,
    hash_sha256: sha(diff),
    diff,
    diff_truncado: false,
    ...extra,
  };
}
const DIFF_A = "--- a/a.py\n+++ b/a.py\n@@ -3,2 +3,3 @@\n x = 1\n-y = 2\n+y = 3\n+z = 4\n";
const DIFF_B = "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-<script>alert(1)</script>\n+ok\n";

function responder(cambios: Cambio[]) {
  server.use(
    http.get("*/api/v1/audit/tickets/:numero", () => HttpResponse.json({ ...expediente, cambios })),
  );
}
async function abrir() {
  const usuario = await entrarDesde("/auditoria/KC-1001");
  await screen.findByTestId("expediente.cambios.lista");
  return usuario;
}
const archivos = () => screen.getAllByTestId("expediente.cambios.archivo");
const sellos = () => screen.getAllByTestId("expediente.cambios.verificacion");

describe("visor de diffs", () => {
  it("con un solo archivo lo muestra abierto, numerado y con su sello verificado", async () => {
    responder([cambio("a.py", DIFF_A)]);
    await abrir();

    expect(archivos()[0]).toHaveAttribute("aria-expanded", "true");
    expect(archivos()[0]).toHaveTextContent("a.py+2 −1");
    expect(archivos()[0]).toHaveAccessibleName(/a\.py.*2 líneas agregadas y 1 eliminadas/);
    const tabla = screen.getByRole("table", { name: "Diff de a.py" });
    const filas = within(tabla)
      .getAllByRole("row")
      .slice(1)
      .map((f) => [
        f.className,
        ...[...(f as HTMLTableRowElement).cells].map((c) => c.textContent),
      ]);
    expect(filas).toEqual([
      ["diff-cabecera", "", "", "--- a/a.py"],
      ["diff-cabecera", "", "", "+++ b/a.py"],
      ["diff-bloque", "", "", "@@ -3,2 +3,3 @@"],
      ["diff-contexto", "3", "3", " x = 1"],
      ["diff-eliminada", "4", "", "-y = 2"],
      ["diff-agregada", "", "4", "+y = 3"],
      ["diff-agregada", "", "5", "+z = 4"],
    ]);
    await waitFor(() => expect(sellos()[0]).toHaveTextContent("✓ Coincide con su sello SHA-256"));
    expect(screen.getByText("Racional de a.py")).toBeInTheDocument();
    expect(screen.getByTestId("expediente.cambios.pr")).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
  });

  it("con varios archivos se abren uno por uno", async () => {
    responder([cambio("a.py", DIFF_A), cambio("b.py", DIFF_B)]);
    const usuario = await abrir();

    expect(archivos().map((a) => a.getAttribute("aria-expanded"))).toEqual(["false", "false"]);
    expect(screen.queryByTestId("expediente.cambios.diff")).not.toBeInTheDocument();

    await usuario.click(archivos()[1]!);

    expect(archivos()[1]).toHaveAttribute("aria-expanded", "true");
    const tabla = screen.getByTestId("expediente.cambios.diff");
    expect(archivos()[1]).toHaveAttribute("aria-controls", tabla.parentElement!.id);
    // El diff viene del repositorio del cliente: es texto, no HTML.
    expect(within(tabla).getByText("-<script>alert(1)</script>")).toBeInTheDocument();
    expect(tabla.querySelector("script")).toBeNull();

    await usuario.click(archivos()[1]!);
    expect(screen.queryByTestId("expediente.cambios.diff")).not.toBeInTheDocument();
  });

  it("si el diff no coincide con su sello, lo dice", async () => {
    responder([cambio("a.py", DIFF_A, { hash_sha256: sha(DIFF_B) })]);
    await abrir();

    await waitFor(() =>
      expect(sellos()[0]).toHaveTextContent(
        "✗ No coincide con su sello SHA-256: lo que se muestra no es lo registrado",
      ),
    );
    expect(sellos()[0]).toHaveClass("no-coincide");
  });

  it("un diff cortado se muestra con aviso y sin verificar", async () => {
    responder([
      cambio("a.py", DIFF_A, { hash_sha256: sha(DIFF_A + "resto"), diff_truncado: true }),
    ]);
    await abrir();

    expect(sellos()[0]).toHaveTextContent("Sin verificar: el diff está cortado");
    expect(screen.getByText(/se muestran sus primeros .+ caracteres/)).toBeInTheDocument();
  });

  it("no enlaza un pr_url que no sea http(s)", async () => {
    responder([cambio("a.py", DIFF_A, { pr_url: "javascript:alert(1)" })]);
    await abrir();

    expect(screen.queryByTestId("expediente.cambios.pr")).not.toBeInTheDocument();
  });

  it("sin cambios muestra el estado vacío", async () => {
    responder([]);
    await abrir();

    expect(screen.getByTestId("expediente.cambios.vacio")).toBeInTheDocument();
  });
});
