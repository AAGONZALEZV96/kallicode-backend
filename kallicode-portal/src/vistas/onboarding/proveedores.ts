/**
 * Proveedores de conexión y sus campos (KC-PT-07-2).
 *
 * El backend no define qué lleva cada proveedor (`config` y `credenciales` son libres en
 * ConexionIn), así que este catálogo es la propuesta del portal, alineada con lo que ya
 * leen sus consumidores:
 *   - CodeMapping (secretos.py) lee de la bóveda `repo_url` y `db_url`;
 *   - el receptor de webhooks lee `webhook_secret`, `api_token` o `token`;
 *   - la prueba de conexión (integraciones.py) mira `usuario` en las bases de datos.
 * Los proveedores por categoría son los de connections._PROVEEDORES.
 */

export type Categoria = "repositorio" | "tickets" | "cicd" | "database";
export type Destino = "config" | "credenciales";

export interface Campo {
  clave: string;
  destino: Destino;
  tipo: "url" | "texto" | "numero" | "secreto";
  requerido: boolean;
  /** Valor de ejemplo que se muestra vacío en el campo. */
  ejemplo?: string;
}

export interface Proveedor {
  id: string;
  categoria: Categoria;
  campos: Campo[];
  /** Ruta de su webhook entrante (webhooks.py), si recibe eventos. */
  webhook?: string;
  /** Esquema de la URL de conexión que arma el portal para CodeMapping (db_url). */
  esquemaBd?: string;
}

const url = (clave: string, ejemplo: string): Campo => ({
  clave,
  destino: "config",
  tipo: "url",
  requerido: true,
  ejemplo,
});
const secreto = (clave: string, requerido = true): Campo => ({
  clave,
  destino: "credenciales",
  tipo: "secreto",
  requerido,
});
const secretoWebhook = secreto("webhook_secret", false);
const usuario: Campo = {
  clave: "usuario",
  destino: "credenciales",
  tipo: "texto",
  requerido: true,
};

const repositorio = (id: string, ejemplo: string): Proveedor => ({
  id,
  categoria: "repositorio",
  campos: [url("repo_url", ejemplo), secreto("token")],
});

const baseDeDatos = (id: string, esquemaBd: string, puerto: string): Proveedor => ({
  id,
  categoria: "database",
  esquemaBd,
  campos: [
    { clave: "host", destino: "config", tipo: "texto", requerido: true, ejemplo: "db.empresa.com" },
    { clave: "puerto", destino: "config", tipo: "numero", requerido: true, ejemplo: puerto },
    { clave: "base", destino: "config", tipo: "texto", requerido: true, ejemplo: "produccion" },
    usuario,
    secreto("contrasena"),
  ],
});

export const PROVEEDORES: Proveedor[] = [
  repositorio("github", "https://github.com/empresa/app"),
  repositorio("gitlab", "https://gitlab.com/empresa/app"),
  repositorio("bitbucket", "https://bitbucket.org/empresa/app"),
  repositorio("corestream_repo", "https://repos.corestream.io/empresa/app"),
  {
    id: "jira",
    categoria: "tickets",
    webhook: "jira",
    campos: [
      url("url", "https://empresa.atlassian.net"),
      { clave: "proyecto", destino: "config", tipo: "texto", requerido: true, ejemplo: "OPS" },
      usuario,
      secreto("api_token"),
      secretoWebhook,
    ],
  },
  {
    id: "github_issues",
    categoria: "tickets",
    webhook: "github",
    campos: [url("repo_url", "https://github.com/empresa/app"), secreto("token"), secretoWebhook],
  },
  {
    id: "kallicode_help",
    categoria: "tickets",
    webhook: "kallicode-help",
    campos: [url("url", "https://help.empresa.com"), secreto("api_token"), secretoWebhook],
  },
  {
    id: "corestream",
    categoria: "tickets",
    webhook: "corestream",
    campos: [url("url", "https://empresa.corestream.io"), secreto("api_token"), secretoWebhook],
  },
  {
    id: "github_actions",
    categoria: "cicd",
    webhook: "ci",
    campos: [url("repo_url", "https://github.com/empresa/app"), secreto("token"), secretoWebhook],
  },
  {
    id: "gitlab_ci",
    categoria: "cicd",
    webhook: "ci",
    campos: [url("repo_url", "https://gitlab.com/empresa/app"), secreto("token"), secretoWebhook],
  },
  {
    id: "jenkins",
    categoria: "cicd",
    webhook: "ci",
    campos: [url("url", "https://ci.empresa.com"), usuario, secreto("api_token"), secretoWebhook],
  },
  baseDeDatos("postgresql", "postgresql", "5432"),
  baseDeDatos("mysql", "mysql", "3306"),
  baseDeDatos("sqlserver", "mssql", "1433"),
  baseDeDatos("oracle", "oracle", "1521"),
];

export const proveedoresDe = (categoria: Categoria) =>
  PROVEEDORES.filter((p) => p.categoria === categoria);

export const proveedor = (id: string) => PROVEEDORES.find((p) => p.id === id);

export type Valores = Record<string, string>;

export interface CuerpoConexion {
  categoria: Categoria;
  proveedor: string;
  nombre?: string;
  config: Record<string, string | number>;
  credenciales: Record<string, string>;
}

/**
 * ConexionIn a partir de lo que se escribió: separa `config` (visible) de `credenciales`
 * (van a la bóveda), omite los opcionales vacíos y agrega lo que lee CodeMapping:
 * `repo_url` en los repositorios y `db_url` en las bases de datos.
 */
export function armarCuerpo(p: Proveedor, valores: Valores, nombre: string): CuerpoConexion {
  const config: Record<string, string | number> = {};
  const credenciales: Record<string, string> = {};
  for (const campo of p.campos) {
    const valor = (valores[campo.clave] ?? "").trim();
    if (!valor) continue;
    if (campo.destino === "credenciales") credenciales[campo.clave] = valor;
    else config[campo.clave] = campo.tipo === "numero" ? Number(valor) : valor;
  }
  if (p.categoria === "repositorio") credenciales.repo_url = String(config.repo_url);
  if (p.esquemaBd) {
    const usuarioBd = encodeURIComponent(credenciales.usuario ?? "");
    const clave = encodeURIComponent(credenciales.contrasena ?? "");
    credenciales.db_url = `${p.esquemaBd}://${usuarioBd}:${clave}@${config.host}:${config.puerto}/${encodeURIComponent(String(config.base))}`;
  }
  return {
    categoria: p.categoria,
    proveedor: p.id,
    ...(nombre.trim() ? { nombre: nombre.trim() } : {}),
    config,
    credenciales,
  };
}

/** Error de un campo antes de enviar, o undefined si está bien. */
export function errorDeCampo(
  campo: Campo,
  valor: string,
): "requerido" | "url" | "puerto" | undefined {
  const v = valor.trim();
  if (!v) return campo.requerido ? "requerido" : undefined;
  if (campo.tipo === "url" && !/^https:\/\/[^\s/]+/.test(v)) return "url";
  if (campo.tipo === "numero" && !(/^\d+$/.test(v) && Number(v) >= 1 && Number(v) <= 65535)) {
    return "puerto";
  }
  return undefined;
}
