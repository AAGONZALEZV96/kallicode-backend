# M3: pipeline hasta Gate 1 firmado (KC-BE-06-6)

El criterio automatizado del hito M3 es `tests/e2e/test_pipeline_hasta_gate1.py`.
Debe pasar antes de declarar el hito en la Sprint Review.

## Recorrido verificado

1. Recibe un webhook GitHub firmado con HMAC SHA-256 y evento `issues`.
2. El normalizador real crea el ticket y su job en `pendiente_asignacion`.
3. Se prepara la asignación inicial a `triage` para el job de prueba. El test
   invoca `procesar_job` real por etapa; no inicia el scheduler global ni workers
   de fondo, por lo que no consume jobs de otros tenants.
4. Triage registra tres pasos y avanza a `design`; diseño registra cuatro pasos,
   publica la spec con dos alternativas y criterios Given/When/Then y llega a `gate1`.
5. Una nueva ejecución del orquestador en el gate no avanza ni consume tokens.
6. Un usuario real con rol `approver`, autenticado por JWT, firma Gate 1 y elige
   la alternativa A. Se verifican la firma, la spec aprobada y la alternativa elegida.
7. La firma habilita `build`. Se ejecutan plan, diff y selfreview grabados hasta `qa`,
   conservando Gate 1 firmado. No se ejecutan QA ni gates posteriores.

El orden del código de producción es **triage → design → Gate 1 → firma → build**.
Build necesita una spec aprobada: no puede exigirse `build.plan` antes de la firma.

En cada etapa se comprueban el estado, las filas exactas de `core.llm_steps`, su tier,
los tokens de entrada/salida y la correspondencia con `core.usage_tokens` (incluidos
modelo, duración y etapa). Se verifica la cadena con `audit.verify_chain` y se enlaza
el sello de la firma con el evento `gate_firmado` del expediente del ticket.

## Grabaciones y dependencias

Los diez archivos de `tests/e2e/grabaciones/` contienen respuestas JSON sintéticas,
sin credenciales ni datos de clientes. Su nombre fija paso y tier. La fixture sustituye
solo el transporte del proveedor LLM (`_llamar`); conserva selección de tier, validación
JSON Schema, versiones de plantillas, orquestador y persistencia reales. No hay fallback
a otro tier o archivo cuando falta una grabación.

PostgreSQL y Redis son reales. Las dos APIs se ejecutan en proceso mediante ASGI,
incluidas autenticación y autorizaciones. Blob usa un almacén en memoria con escritura
y lectura de artefactos y CodeMapping usa su doble de subgrafo vacío. Se habilita
`persistir_tier_fable` exclusivamente durante la prueba, contra el esquema migrado.

Cada ejecución crea un tenant, plan, suscripción, conexión y aprobador propios. Al terminar,
borra sus datos mediante las cascadas del ticket y elimina su secreto de bóveda. La limpieza
de auditoría sigue la convención de fixtures del repositorio: desactiva y restaura su trigger
de inmutabilidad en una única transacción administrativa y borra solo el tenant de prueba.

## Ejecución y CI

Requiere el entorno local del backend configurado (`.env`), PostgreSQL con todos los esquemas
y migraciones aplicados, Redis y `KC_BOVEDA_CLAVE_LOCAL` de desarrollo. Usar una base de pruebas,
sin un normalizador de fondo consumiendo la misma cola.

```bash
uv run pytest tests/e2e/test_pipeline_hasta_gate1.py -v
```

El workflow existente es `.github/workflows/ci.yml` (este repositorio no tiene `backend.yml`).
La matriz `backend` ejecuta el paso **M3 - Pipeline hasta Gate 1 firmado (KC-BE-06-6)**
antes de las pruebas generales, sin `continue-on-error`, con límite de tres minutos y
un timeout de proceso de 175 segundos. Un fallo o timeout hace fallar **CI - backend**.
El límite cubre pytest, fixtures y limpieza; el aprovisionamiento de infraestructura es previo.

Para bloquear el merge, la protección de las ramas debe exigir el check **CI - backend**.
Esa configuración vive en GitHub y no se cambia al editar el YAML; verificarla al integrar
este ticket. El paso genera `pytest-m3.xml` en el runner.
