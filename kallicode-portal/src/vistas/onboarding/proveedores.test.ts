/** Catálogo de proveedores de conexión (KC-PT-07-2): cuerpo de ConexionIn y validación. */
import { describe, expect, it } from "vitest";

import { armarCuerpo, errorDeCampo, proveedor, proveedoresDe, type Campo } from "./proveedores";

const p = (id: string) => proveedor(id)!;

describe("catálogo", () => {
  it("tiene los proveedores de connections._PROVEEDORES por categoría", () => {
    const ids = (c: Parameters<typeof proveedoresDe>[0]) => proveedoresDe(c).map((x) => x.id);
    expect(ids("repositorio")).toEqual(["github", "gitlab", "bitbucket", "corestream_repo"]);
    expect(ids("tickets")).toEqual(["jira", "github_issues", "kallicode_help", "corestream"]);
    expect(ids("cicd")).toEqual(["github_actions", "gitlab_ci", "jenkins"]);
    expect(ids("database")).toEqual(["postgresql", "mysql", "sqlserver", "oracle"]);
  });

  it("los de tickets y CI/CD tienen su ruta de webhook; repositorios y bases no", () => {
    expect(p("jira").webhook).toBe("jira");
    expect(p("github_issues").webhook).toBe("github");
    expect(p("kallicode_help").webhook).toBe("kallicode-help");
    expect(p("jenkins").webhook).toBe("ci");
    expect(p("github").webhook).toBeUndefined();
    expect(p("oracle").webhook).toBeUndefined();
  });
});

describe("armarCuerpo", () => {
  it("un repositorio: repo_url en config y también en credenciales para CodeMapping", () => {
    expect(
      armarCuerpo(p("github"), { repo_url: " https://github.com/a/b ", token: "ghp_1" }, "  "),
    ).toEqual({
      categoria: "repositorio",
      proveedor: "github",
      config: { repo_url: "https://github.com/a/b" },
      credenciales: { token: "ghp_1", repo_url: "https://github.com/a/b" },
    });
  });

  it("omite los opcionales vacíos y lleva el nombre si se escribió", () => {
    const cuerpo = armarCuerpo(
      p("jira"),
      {
        url: "https://x.atlassian.net",
        proyecto: "OPS",
        usuario: "kc",
        api_token: "t",
        webhook_secret: "  ",
      },
      " Jira de OPS ",
    );
    expect(cuerpo).toEqual({
      categoria: "tickets",
      proveedor: "jira",
      nombre: "Jira de OPS",
      config: { url: "https://x.atlassian.net", proyecto: "OPS" },
      credenciales: { usuario: "kc", api_token: "t" },
    });
  });

  it("una base de datos: puerto numérico y db_url con usuario y clave codificados", () => {
    const cuerpo = armarCuerpo(
      p("sqlserver"),
      {
        host: "db.x",
        puerto: "1433",
        base: "ventas 2026",
        usuario: "kc lector",
        contrasena: "p@s/s:",
      },
      "",
    );
    expect(cuerpo.config).toEqual({ host: "db.x", puerto: 1433, base: "ventas 2026" });
    expect(cuerpo.credenciales).toEqual({
      usuario: "kc lector",
      contrasena: "p@s/s:",
      db_url: "mssql://kc%20lector:p%40s%2Fs%3A@db.x:1433/ventas%202026",
    });
  });
});

describe("errorDeCampo", () => {
  const campo = (tipo: Campo["tipo"], requerido = true): Campo => ({
    clave: "x",
    destino: "config",
    tipo,
    requerido,
  });

  it("requerido vacío (o solo espacios) es error; opcional vacío no", () => {
    expect(errorDeCampo(campo("texto"), " ")).toBe("requerido");
    expect(errorDeCampo(campo("secreto", false), "")).toBeUndefined();
  });

  it.each(["http://x.com", "https://", "ftp://x", "github.com/a"])("URL «%s» no vale", (v) => {
    expect(errorDeCampo(campo("url"), v)).toBe("url");
  });

  it("URL https sí vale", () => {
    expect(errorDeCampo(campo("url"), "https://github.com/a/b")).toBeUndefined();
  });

  it.each(["0", "65536", "54a", "-1", "1.5"])("puerto «%s» no vale", (v) => {
    expect(errorDeCampo(campo("numero"), v)).toBe("puerto");
  });

  it.each(["1", "5432", "65535"])("puerto «%s» sí vale", (v) => {
    expect(errorDeCampo(campo("numero"), v)).toBeUndefined();
  });
});
