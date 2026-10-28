-- Migración 026: Ciclo de cuota por aniversario desde subscriptions (KC-DB-05-4)

SET lock_timeout = '5s';
SET statement_timeout = '30s';



CREATE OR REPLACE FUNCTION core.obtener_ciclo_cuota_actual(p_tenant_id text, p_fecha date DEFAULT CURRENT_DATE)
RETURNS text
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_inicia_el date;
    v_dia_aniv int;
    v_anio int;
    v_mes int;
    v_ultimo_dia_mes int;
    v_dia_objetivo int;
    v_ciclo_inicio date;
BEGIN
    -- 1. Buscar la suscripción vigente del tenant
    SELECT inicia_el INTO v_inicia_el
    FROM core.subscriptions
    WHERE tenant_id = p_tenant_id
      AND finaliza_el IS NULL
      AND estado IN ('activa', 'periodo_gracia')
    LIMIT 1;

    IF v_inicia_el IS NULL THEN
        RETURN to_char(p_fecha, 'YYYY-MM');
    END IF;

    v_dia_aniv := EXTRACT(DAY FROM v_inicia_el)::int;
    v_anio := EXTRACT(YEAR FROM p_fecha)::int;
    v_mes := EXTRACT(MONTH FROM p_fecha)::int;

    -- Determinar el último día del mes actual evaluado
    v_ultimo_dia_mes := EXTRACT(DAY FROM (make_date(v_anio, v_mes, 1) + INTERVAL '1 month - 1 day'))::int;
    v_dia_objetivo := LEAST(v_dia_aniv, v_ultimo_dia_mes);

    -- Si la fecha actual aún no alcanza el día objetivo en el mes corriente,
    -- el ciclo actual empezó en el mes anterior
    IF EXTRACT(DAY FROM p_fecha)::int < v_dia_objetivo THEN
        p_fecha := (p_fecha - INTERVAL '1 month')::date;
        v_anio := EXTRACT(YEAR FROM p_fecha)::int;
        v_mes := EXTRACT(MONTH FROM p_fecha)::int;
        
        v_ultimo_dia_mes := EXTRACT(DAY FROM (make_date(v_anio, v_mes, 1) + INTERVAL '1 month - 1 day'))::int;
        v_dia_objetivo := LEAST(v_dia_aniv, v_ultimo_dia_mes);
    END IF;

    v_ciclo_inicio := make_date(v_anio, v_mes, v_dia_objetivo);

    RETURN to_char(v_ciclo_inicio, 'YYYY-MM-DD');
END;
$$;

GRANT EXECUTE ON FUNCTION core.obtener_ciclo_cuota_actual(text, date) TO kallicode_app, kallicode_worker;

