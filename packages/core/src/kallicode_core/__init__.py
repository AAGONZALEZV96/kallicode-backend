"""kallicode_core — núcleo compartido del backend Kallicode.

Módulos:
    config     Configuración KC_* (pydantic-settings).
    logging    Log central estructurado (todas las transacciones).
    errors     Catálogo de errores y sobre JSON estándar.
    db         Sesiones PostgreSQL con RLS por tenant.
    ids        ULIDs prefijados.
    seguridad  JWT RS256, Argon2id, roles y firmas de gates.
    auditoria  Cadena de auditoría inmutable (hash encadenado).
    comercial  Umbrales de uso por plan (QU-1/2/3, RL-1/2/3).
    llm        Enrutador de 3 tiers y catálogo de llamadas LLM documentadas.
"""
__version__ = "0.1.0"
