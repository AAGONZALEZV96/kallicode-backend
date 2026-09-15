# INSTRUCTIVO DE GOBERNANZA GIT Y FLUJO DE TRABAJO

**Monorepo:** `kallicode-backend`  
**Equipo 5:** Plataforma y Confianza  

Este documento establece las políticas de control de versiones, flujos de trabajo (Git Workflows) y mecanismos de protección de código aplicados al monorepo central de KalliCode.  
Al ser una plataforma multi-tenant compleja con estrictos requisitos de seguridad, aislamiento de datos (RLS) e integridad criptográfica, es imperativo que todo desarrollador cumpla rigurosamente con este estándar para evitar regresiones de seguridad o fallos en la base de datos de producción.

---

## 1. Modelo de Ramas (Git Branching Model)

El proyecto adopta un modelo híbrido basado en **Feature Branches** con una ruta de integración lineal hacia la rama de desarrollo.

[main] -------------------------------------------------------- (Producción / Release GA)
^
[develop] ----------------------------------------------------- (Integración / CI activa)
\                                      /
[feature/*]   \---> [feature/seguridad-jwt] -------> (PR con CodeOwners + CI aprobada)


- **main (Rama de Producción):** Contiene exclusivamente código estable, auditado y listo para su despliegue final. Nadie puede realizar commits directos ni fusiones manuales sobre esta rama.
- **develop (Rama de Integración):** Rama activa donde convergen las características de todos los equipos. Todo cambio propuesto debe pasar por aquí para validar su estabilidad en CI.
- **feature/* (Ramas de Características):** Ramas efímeras creadas para resolver un ticket específico del backlog. Se derivan siempre de `develop` y se reintegran mediante una Pull Request (PR).

---

## 2. Configuración de Reglas de Protección de Ramas (GitHub)

Las ramas `main` y `develop` cuentan con **Branch Protection Rules** estrictas:

- ✅ Require a pull request before merging  
- ✅ Required Approvals (mínimo 1 revisor)  
- ✅ Require review from Code Owners  
- ✅ Require status checks to pass before merging (CI en verde: Ruff, Mypy, Gitleaks, Semgrep, Pytest)  
- ✅ Require conversation resolution before merging  
- ✅ Require linear history (Squash & Merge o Rebase)  
- ✅ Enforce rules for administrators  

---

## 3. El Escudo de Gobernanza: `.github/CODEOWNERS`

El archivo `CODEOWNERS` define la propiedad intelectual y técnica de los componentes críticos:

- `/db/` → Equipo 5 (Miembro A prioritario). Protege tablas de inquilinos y políticas RLS.  
- `/packages/core/` → Equipo 5 (Miembros A y C prioritarios). Protege autenticación, JWT y criptografía.  
- Archivos de infraestructura (`docker-compose.yml`, `Makefile`, `pyproject.toml`, `.github/workflows/`) → Equipo 5 (Miembro B prioritario - Alejandro). Protege entornos locales y CI/CD.  

---

## 4. Guía de Trabajo Paso a Paso para el Desarrollador

### Paso 1: Actualizar el entorno local
```bash
git checkout develop
git pull origin develop

``` 
### Paso 2: Crear la rama de trabajo
Crea tu rama utilizando el ID del ticket de tu backlog y un nombre descriptivo corto:

```bash
git checkout -b feature/KC-PLT-01-1-rls-inicial

```
### Paso 3: Sincronizar y desarrollar con uv
Si agregas dependencias o necesitas levantar el entorno local, utiliza los automatizadores provistos por el Miembro B:

```bash
make bootstrap     # Sincroniza el entorno virtual local de Python con uv
make up            # Levanta Postgres, Redis, Azurite y Mailpit en Docker
make db            # Aplica el esquema de datos y las semillas de prueba
```

### Paso 4: Validaciones de calidad locales (Pre-Commit)
No subas código roto. Antes de hacer commit, ejecuta las validaciones locales para garantizar que pasarás la CI:
```bash
uv run ruff check .          # Linter estático rápido
uv run ruff format --check . # Validador de formato de código
uv run mypy --strict packages/core/src # Validador estricto de tipado

```

### Paso 5: Confirmar cambios (Mensajes de Commit Estructurados)
Sigue el estándar de Conventional Commits para facilitar la lectura del historial de cambios:
```bash
# Para nuevas funcionalidades en el Core:
git commit -m "feat(core): agregar middleware de extraccion de tenant para fastapi"

# Para correcciones en base de datos:
git commit -m "fix(db): corregir politica RLS de la tabla tickets"

# Para subir documentación sin disparar la CI innecesariamente (ahorro de tokens):
git commit -m "docs: actualizar manual de gobernanza [skip ci]"
```
### Paso 6: Subir la rama y abrir la Pull Request
Envía tu rama al repositorio remoto en GitHub:
```bash
git push origin feature/KC-PLT-01-1-rls-inicial
```
Ve a la interfaz de GitHub, abre una Pull Request hacia la rama develop, y el sistema disparará automáticamente el pipeline de CI y notificará a tus Code Owners para la revisión del código

