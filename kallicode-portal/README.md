# Kallicode Portal

Portal Cliente de Kallicode: SPA React 18 + Vite + TypeScript, fiel al
wireframe v0.1 (tokens CSS, modo claro/oscuro, i18n es/en/pt). Consume el
backend `kallicode-backend` (/api/v1) con JWT y refresh rotativo automático.

## Arranque local

```bash
# 1) backend corriendo en :8000 (ver kallicode-backend/README.md)
npm install
npm run dev        # http://localhost:5173 (proxy /api -> :8000)
```

Login con los usuarios seed del backend (p. ej. `owner@demo.kallicode.dev`
/ `Dev12345!demo`).

## Calidad de código

```bash
npm test              # Vitest + Testing Library contra dobles MSW (sin backend)
npm run lint          # ESLint (TS, react-hooks, jsx-a11y) sin warnings
npm run format        # Prettier sobre todo el portal (format:check para CI)
```

`npm install` activa un hook pre-commit (Husky + lint-staged) que corre ESLint y
Prettier sobre los archivos del portal en stage. Node: ver `.nvmrc`.

## Build de producción

```bash
npm run build      # tsc -b && vite build -> dist/
```

En producción, servir `dist/` (Azure Static Web Apps o el mismo Container
Apps environment) y fijar `VITE_API_URL` al dominio de la API.

## Estructura

```
src/api/         client.ts (fetch + refresh + sobre de errores) · tipos.ts
src/estado/      auth.tsx (sesión) · i18n.tsx (es/en/pt)
src/estilos/     tokens.css (variables del wireframe, claro/oscuro)
src/componentes/ Layout (nav + badges por polling) · ui.tsx (chips, KPI, errores)
src/vistas/      12 pantallas: Login, Tablero, Tickets, NuevoTicket,
                 NuevaFuncionalidad, DetalleTicket (5 pestañas + panel de gate),
                 Aprobaciones, Auditoria, Expediente, Conexiones, ModelosIA, Consumo
```

## Decisiones

- **Polling, no tiempo real (D12):** badges cada 30 s, tablero cada 60 s,
  con debounce de 600 ms en dedup/impact preview (respeta RL-2 del backend).
- **Errores:** el sobre `{error:{codigo,mensaje,trace_id}}` del backend se
  muestra tal cual (componente `CajaError`), con el trace_id para soporte.
- **Roles:** el panel de gate solo aparece si el rol puede firmar ese gate
  (Gate 3 = architect); la API igualmente lo valida en servidor.
- **404 de pestañas:** spec/evidencia/seguridad devuelven 404 hasta que la
  etapa ocurre — el portal lo trata como «aún sin datos», no como error.
- **Cuota (QU-1):** una creación con HTTP 202 muestra el aviso de
  `en_cola_por_cuota`; la barra de cuota en Consumo cambia de color al 80/100%.
