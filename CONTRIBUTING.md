# Guía de Contribución y Estrategia de Ramas — KalliCode

Este repositorio utiliza un flujo de trabajo basado en **Trunk-Based Development adaptado**. Todo el código nuevo debe integrarse continuamente en `develop` para garantizar que la infraestructura local y los despliegues de desarrollo (`dev`) siempre funcionen[cite: 2].

## 1. Ramas Base (Protegidas)
- **`develop`**: Rama por defecto. Contiene el código más reciente con las pruebas en verde. Todo Pull Request debe apuntar aquí[cite: 2].
- **`main`**: Rama de producción y staging. Solo se actualiza mediante merges desde `develop`[cite: 2]. Nunca se hacen commits directos aquí.

## 2. Nomenclatura de Ramas de Trabajo
Para aislar el desarrollo y evitar colisiones, cada integrante debe crear una rama efímera a partir de `develop`. El formato estricto es `feature/<equipo>-<tarea>` o `fix/<equipo>-<tarea>`[cite: 2].

**Ejemplos por Equipo:**
- **Equipo 1 (CodeMapping):** `feature/e1-ingesta-repo`, `feature/e1-grafo-neo4j`
- **Equipo 2 (Fábrica):** `feature/e2-orquestador-llm`, `fix/e2-cola-redis`
- **Equipo 3 (QA y Gates):** `feature/e3-sandbox-eval`, `feature/e3-firmas-humanas`
- **Equipo 4 (Producto/Portal):** `feature/e4-kanban-vistas`, `feature/e4-auth-ui`
- **Equipo 5 (Plataforma y Confianza - DevOps/DB/Sec):** `feature/e5-infra-docker`, `feature/e5-rls-policies`, `feature/e5-jwt-rotation`
- **Equipo 6 (Consola Interna):** `feature/e6-admin-rbac`, `feature/e6-telemetria`

## 3. Convención de Commits
Este repositorio exige *Conventional Commits*. El linter `pre-commit` rechazará cualquier commit que no siga esta estructura para poder generar los *changelogs* automáticamente[cite: 2, 8].

- `feat:` -> Nueva funcionalidad (ej. `feat: añade orquestador de contenedores efímeros`).
- `fix:` -> Corrección de un error (ej. `fix: resuelve conexión con Redis en workers`).
- `db:` -> Cambios en esquemas o migraciones SQL (ej. `db: añade política RLS para tenants`).
- `ci:` -> Cambios en Docker, Makefile o CI/CD (ej. `infra: configura gitleaks en pipeline`).
- `docs:` -> Cambios exclusivos en documentación.

## 4. Ciclo de Vida del Pull Request (PR)
1. Actualiza tu rama local: `git checkout develop && git pull origin develop`.
2. Crea tu rama: `git checkout -b feature/e5-nueva-tarea`.
3. Desarrolla y haz commits convencionales.
4. Sube la rama: `git push -u origin feature/e5-nueva-tarea`.
5. Abre el PR en GitHub apuntando a `develop`[cite: 2].
6. **Puertas automáticas:** El CI ejecutará Ruff, Mypy, Gitleaks, Semgrep y Pytest[cite: 2, 8]. El merge se bloqueará si alguna validación falla o la cobertura baja del 80%[cite: 2].
7. **Revisión Humana:** Requiere al menos una aprobación de un par. Cambios en seguridad, BD o auditoría exigen aprobación del Tech Lead.