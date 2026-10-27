// ESLint del portal (KC-PT-01-1): TypeScript + reglas de hooks + accesibilidad.
// eslint-config-prettier va al final para que el formato lo decida solo Prettier.
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  // src/api/generado: tipos generados desde el contrato OpenAPI (npm run tipos:api).
  { ignores: ["dist", "coverage", "src/api/generado"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      jsxA11y.flatConfigs.recommended,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      // Solo las dos reglas clásicas de hooks; las del React Compiler (v7) quedan fuera de alcance.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      // useAuth/useI18n conviven con su Provider (patrón de contexto), igual que el RBAC
      // (PT-F06); fechaCorta se mueve a formateadores centralizados en KC-PT-09-3.
      "react-refresh/only-export-components": [
        "warn",
        {
          allowConstantExport: true,
          allowExportNames: [
            "useAuth",
            "useI18n",
            "DICT",
            "fechaCorta",
            "MAPA_RUTAS_ROLES",
            "puedeVerRuta",
            "permiteAccion",
            "rolActual",
            "usePermiso",
          ],
        },
      ],
      // CLAUDE.md: evitar `any` y `as`. Los `as` existentes se erradican en KC-PT-11-3.
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-assertions": [
        "warn",
        { assertionStyle: "as", objectLiteralTypeAssertions: "never" },
      ],
    },
  },
  // Utilidades de prueba: nunca pasan por fast refresh, pueden mezclar componentes y funciones.
  {
    files: ["src/test/**/*.{ts,tsx}"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  prettier,
);
