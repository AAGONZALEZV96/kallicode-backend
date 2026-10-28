# Pruebas de mutación y cobertura de CodeMapping (KC-CM-06-6)

Sprint 6 · Carril CodeMapping · Responsable: Francisco Salinas (QA)
Depende de: KC-CM-06-4 (contrato de stats) y KC-CM-06-5 (umbral por tenant), ya integrados.

## 1. Resultado

| Métrica | Antes | Después |
|---|---|---|
| Cobertura de `packages/codemapping` (líneas + ramas, sin Postgres) | 51 % | **79 %** |
| Compuerta de cobertura en CI (`codemapping`) | 0 % (desactivada) | **70 %** |
| Mutantes evaluados (4 módulos) | 336 | 336 |
| Mutantes muertos | 222 (66 %) | **325 (96,7 %)** |
| Compuerta de mutación en CI | no existía | **90 %** |

La cobertura se mide **sin base de datos**: los 15 tests de integración que necesitan Postgres
(`graph_loader`, `extract_sql`, `rls_cm`, `build_graph_resume`) se omiten en local y sí corren en CI,
así que el valor real en CI es igual o mayor.

## 2. Qué se agregó

| Archivo | Para qué |
|---|---|
| `test/test_build_graph_unit.py` | `full_build()` con las fronteras (git, Postgres, LLM, TEI) reemplazadas por dobles: 7 fases, checkpoint en disco, reanudación, presupuesto, fallos y CLI. |
| `test/test_refresh_unit.py` | `refresh.py`: diff de git, carga de archivos, refresco incremental, fallback a full build. |
| `test/test_infraestructura_unit.py` | `cm_db`, `checkpoints`, `logging_setup`, `embeddings` (con `httpx.MockTransport`) y `extract_sql`. |
| `test/test_mutacion_dirigida.py` | Una prueba por cada grupo de mutantes que sobrevivió a la primera corrida: valores exactos, límites, claves y argumentos. |
| `test/test_verificar_mutacion.py` | Pruebas de la compuerta de mutación. |
| `scripts/verificar_mutacion.py` | Lee `mutmut results`, calcula el puntaje y falla bajo el mínimo. |
| `mutmut_pytest.ini` | Configuración de pytest exclusiva de mutmut (ver 4). |

## 3. Cómo correr las pruebas de mutación

`mutmut` no funciona en Windows nativo: usar **WSL**, Linux o macOS.

```bash
make mutation-codemapping
# equivale a:
cd packages/codemapping
uv run --with "mutmut>=3.8,<4" mutmut run
uv run --with "mutmut>=3.8,<4" python scripts/verificar_mutacion.py --minimo 90
```

Para ver un mutante que sobrevivió: `uv run --with "mutmut>=3.8,<4" mutmut show <nombre>`.

## 4. Decisiones

- **Alcance de la mutación**: `budget`, `checkpoints`, `embeddings`, `tenant_thresholds`. Son lógica
  pura con pruebas unitarias sin BD, donde un mutante vivo indica una aserción floja de verdad.
  `graph_loader` y `snapshots` se dejan fuera porque dependen de Postgres y harían la corrida lenta.
  Para ampliar el alcance, agregar el archivo a `only_mutate` y sus pruebas a
  `pytest_add_cli_args_test_selection` en `packages/codemapping/pyproject.toml`.
- **`mutmut_pytest.ini`**: sin él, pytest sube hasta el `pyproject.toml` raíz, cuyo `pythonpath`
  apunta al código original, y mutmut importa el código sin mutar (resultado: "no tests").
- **Mínimo de 90 % y no 100 %**: los 11 mutantes que quedan vivos son equivalentes (no cambian el
  comportamiento observable):
  - `encoding="utf-8"` -> `"UTF-8"` / valor por defecto (6 casos; `json.dumps` produce solo ASCII).
  - `[:1000]` -> `[:1001]` y `/ 1_000_000.0` -> `/ 1_000_001.0`: caen en el mismo valor tras `ceil` / `round`.
  - `s.get("signature", None)` y `s.get("signature", )`: `estimar_tokens_texto(None)` devuelve 0, igual que con `""`.
  - `if row and row[0] is not None` -> `if row or ...`: ambos caminos terminan en el umbral por defecto.
- **Compuerta de cobertura solo para `codemapping`**: el resto de los proyectos conserva el
  `--cov-fail-under=0` temporal que dejó QA (marcado "SACAR DESPUES") hasta que cada equipo mida su línea base.

## 5. Hallazgos para el equipo

1. `build_graph.py:239` llama `marcar_fallido(motivo=...)` pero el parámetro se llama `error_msg`:
   `TypeError` en la rama de presupuesto insuficiente. Sigue marcado como `xfail(strict=True)` en
   `test_contrato_stats_ingesta.py`. Los tests de `full_build` de este ticket usan dobles sin firma,
   por lo que no lo detectan: al corregirlo, quitar el `xfail` y reforzar el doble con `create_autospec`.
2. `[tool.coverage.run] source = ["packages/core"]` en el `pyproject.toml` raíz no incluye `codemapping`,
   y `fail_under = 80` contradice el comentario (línea base 64 %). `make test` mide solo `core`.
3. `graph_loader.py` queda en 20 % sin Postgres. Las pruebas de integración lo cubren en CI; cualquier
   ampliación futura de cobertura debe pasar por ahí.
