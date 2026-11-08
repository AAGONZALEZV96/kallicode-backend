# KC-BE-05-6: aislamiento inicial de la API interna

## Aplicaciones ASGI

| Superficie | Aplicación | Puerto previsto |
| --- | --- | --- |
| Portal y webhooks | `kallicode_api.main:app` | 8000 |
| Pipeline interno | `kallicode_api.main_interna:app` | 8001 |

La aplicación pública no registra ninguna operación de `/internal/v1`.

La aplicación interna registra las operaciones del pipeline y los
endpoints de salud. No registra las operaciones del portal ni los
webhooks. Su documentación HTTP está desactivada.

## Ejecución local

Ejecutar cada comando en una terminal independiente:

```bash
uv run uvicorn kallicode_api.main:app \
  --host 127.0.0.1 \
  --port 8000
```

```bash
uv run uvicorn kallicode_api.main_interna:app \
  --host 127.0.0.1 \
  --port 8001 \
  --no-proxy-headers
```

Los workers locales deben usar como URL interna:

`http://localhost:8001/internal/v1`

Si `KC_INTERNAL_API_BASE_URL` ya está definida en el entorno,
debe actualizarse para apuntar al listener interno.

## Identidades de servicio

Los valores permitidos del claim `svc` son:

- `orquestador`
- `normalizador`
- `motor-pruebas`
- `ingesta`

El claim `sub` debe corresponder a `svc:<identidad>`.

Un token de servicio válido con identidad no declarada recibe 403.
Un token de usuario enviado a una operación interna recibe 401.

Las operaciones internas conservan la exigencia de Idempotency-Key.

## Claves, audiencia y emisor

Los JWT de usuario utilizan:

- Emisor: `kc-portal`.
- Audiencia: `kc-portal-api`.
- Tipo: `user`.
- Claves: `KC_JWT_PRIVATE_KEY` y `KC_JWT_PUBLIC_KEY`.

Los JWT de servicio utilizan:

- Emisor: `kc-internal`.
- Audiencia: `kc-internal-api`.
- Tipo: `svc`.
- Claves: `KC_JWT_PRIVATE_KEY_SVC` y `KC_JWT_PUBLIC_KEY_SVC`.

Ambos pares deben ser RSA, tener al menos 2048 bits y ser distintos.

Fuera de local/test, los dos pares son obligatorios.

Las claves efímeras de local/test pertenecen a cada proceso.
Para comunicar workers y API interna en procesos diferentes deben
configurarse claves persistentes coincidentes para los servicios.

## Contratos y pruebas

Regenerar los contratos:

```bash
uv run python docs/congelar_contrato.py
```

Revisar y versionar los YAML generados en `contratos/`.

Ejecutar la prueba de aislamiento:

```bash
uv run pytest tests/seguridad/test_superficie_interna.py -q
```

Con PostgreSQL, Redis y las fixtures de integración disponibles:

```bash
uv run pytest apps/api/tests -q
```

La prueba de aislamiento recorre todas las operaciones internas
registradas. Actualmente existen 15 operaciones.

## Dependencia de Infra/DevOps

El despliegue debe ejecutar las dos aplicaciones en listeners
independientes.

El ingress público debe dirigir tráfico únicamente al listener
público. El listener interno no debe publicarse mediante ingress,
puerto de host público ni balanceador público.

Estos cambios de infraestructura quedan fuera de la implementación
de backend aquí documentada y requieren validación de Infra/DevOps.

La red privada definitiva y mTLS quedan para Sprint 11.

## Revisión con Tech Lead de Testing

Estado: pendiente.

Registrar en el ticket:

- Fecha y responsables de la revisión.
- Resultado de las pruebas de aislamiento e integración.
- Confirmación de 404 para rutas internas en la aplicación pública.
- Confirmación de 401 para tokens de usuario en operaciones internas.
- Confirmación de 403 para identidades de servicio no declaradas.
- Evidencia de los dos listeners desplegados.
- Evidencia de que el ingress publica únicamente el listener público.

El ticket completo requiere también la validación del despliegue.