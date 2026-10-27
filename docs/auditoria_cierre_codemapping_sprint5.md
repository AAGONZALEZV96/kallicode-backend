# Auditoría Formal de Cierre: 13 Hallazgos de CodeMapping (KC-CM-05-6)

| ID Defecto | Ticket Asociado | Componente / Archivo | Estado | Evidencia de Prueba |
| :--- | :--- | :--- | :--- | :--- |
| **CM-F01** | KC-CM-01-4 | `config.py` | CERRADO | Obligatoriedad estricta de `tenant_id` sin credenciales default. |
| **CM-F02** | KC-CM-01-4 | `secretos.py` | CERRADO | `SecretStr` y enmascaramiento con `test_secretos_boveda.py`. |
| **CM-F03** | KC-CM-03-2 | `snapshots.py` | CERRADO | Snapshots transaccionales en `cm.repo_snapshots`. |
| **CM-F04** | KC-CM-04-1 | `parser_treesitter.py` | CERRADO | Streaming por lotes con pool de subprocesos. |
| **CM-F05** | KC-CM-04-3 | `extract_sql.py` | CERRADO | Transacción de solo lectura en esquema cliente. |
| **CM-F06** | KC-CM-05-3 | `llm_enrich.py` | CERRADO | Inferencia unificada vía `ejecutar_paso` con reintentos y escalado. |
| **CM-F07** | KC-CM-05-5 | `budget.py` | CERRADO | `dry-run` de tokens y corte preventivo `BudgetExceededError`. |
| **CM-F08** | KC-CM-05-4 | `prompts.py` | CERRADO | Delimitación estricta y suite de 20 casos de prompt injection. |
| **CM-F09** | KC-CM-03-1 | `graph_loader.py` | CERRADO | Inserción masiva optimizada por `COPY`. |
| **CM-F10** | KC-CM-04-4 | `snapshots.py` | CERRADO | Telemetría estructurada JSONB propagando `trace_id`. |
| **CM-F11** | KC-CM-04-1 | `ingest_repo.py` | CERRADO | Control de memoria y recolección de basura por lotes. |
| **CM-F12** | KC-CM-04-2 | `ingest_repo.py` | CERRADO | Mapeo de ramas por tenant y degradación ante force-push. |
| **CM-F13** | KC-CM-04-5 | `parser_treesitter.py` | CERRADO | Cota de recursión AST (profundidad 50) y firmas canónicas. |
