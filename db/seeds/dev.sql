-- Seeds de desarrollo: tenant demo con usuarios de cada rol y tickets del wireframe.
-- Contraseña de todos los usuarios: Dev12345!demo
-- (hash Argon2id generado con argon2-cffi; ver README)
BEGIN;

INSERT INTO core.organizations (id, nombre, idioma, timezone, fabrica_activa)
VALUES ('org_demo', 'Demo Mobility', 'es', 'Europe/Madrid', true)
ON CONFLICT DO NOTHING;

INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el)
VALUES ('sub_demo', 'org_demo', 'growth', date_trunc('month', current_date)::date,
        (date_trunc('month', current_date) + interval '1 month')::date)
ON CONFLICT DO NOTHING;

INSERT INTO core.users (id, tenant_id, email, nombre, rol, password_hash) VALUES
  ('u_owner', 'org_demo', 'owner@demo.kallicode.dev',      'Olga Owner',      'owner',     '$argon2id$v=19$m=65536,t=3,p=4$x4LGXSu8a0JbK0/auuCTOg$Ph6o1yNS8S1HpOnq0t6pZ9QqACLcdSAfBFDhw0J3+ow'),
  ('u_admin', 'org_demo', 'admin@demo.kallicode.dev',      'Ana Admin',       'admin',     '$argon2id$v=19$m=65536,t=3,p=4$x4LGXSu8a0JbK0/auuCTOg$Ph6o1yNS8S1HpOnq0t6pZ9QqACLcdSAfBFDhw0J3+ow'),
  ('u_arch',  'org_demo', 'arquitecto@demo.kallicode.dev', 'Max K.',          'architect', '$argon2id$v=19$m=65536,t=3,p=4$x4LGXSu8a0JbK0/auuCTOg$Ph6o1yNS8S1HpOnq0t6pZ9QqACLcdSAfBFDhw0J3+ow'),
  ('u_appr',  'org_demo', 'aprobador@demo.kallicode.dev',  'Alicia Approver', 'approver',  '$argon2id$v=19$m=65536,t=3,p=4$x4LGXSu8a0JbK0/auuCTOg$Ph6o1yNS8S1HpOnq0t6pZ9QqACLcdSAfBFDhw0J3+ow'),
  ('u_dev',   'org_demo', 'dev@demo.kallicode.dev',        'María G.',        'member',    '$argon2id$v=19$m=65536,t=3,p=4$x4LGXSu8a0JbK0/auuCTOg$Ph6o1yNS8S1HpOnq0t6pZ9QqACLcdSAfBFDhw0J3+ow')
ON CONFLICT DO NOTHING;

INSERT INTO core.production_lines (tenant_id, numero) VALUES
  ('org_demo', 1), ('org_demo', 2), ('org_demo', 3) ON CONFLICT DO NOTHING;

INSERT INTO core.branch_map (tenant_id) VALUES ('org_demo') ON CONFLICT DO NOTHING;

INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, prioridad, etapa,
                          origen, reportado_por) VALUES
  ('tk_d1', 'org_demo', 'bug', 'Timeout en búsqueda de clientes',
   'La búsqueda tarda más de 30 segundos con clientes corporativos.', 'alta',
   'triage', 'portal', 'u_dev'),
  ('tk_d2', 'org_demo', 'mejora', 'Exportar CSV desde listados',
   'Necesitamos exportar los listados a CSV.', 'baja', 'triage', 'portal', 'u_dev'),
  ('tk_d3', 'org_demo', 'funcionalidad', 'Facturación por convenio corporativo',
   'Las empresas con convenio necesitan una factura mensual consolidada.', 'media',
   'design', 'portal', 'u_dev')
ON CONFLICT DO NOTHING;

COMMIT;
