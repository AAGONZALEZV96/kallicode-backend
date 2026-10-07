// @vitest-environment node

// Simulamos el almacenamiento del navegador para que src/test/setup.ts no falle
const mockStorage = { clear: () => {}, getItem: () => null, setItem: () => {}, removeItem: () => {} };
globalThis.sessionStorage = mockStorage as any;
globalThis.localStorage = mockStorage as any;

import { describe, test, expect } from 'vitest';
import { MAPA_RUTAS_ROLES, type Rol } from './auth';

describe('Matriz de Control de Acceso (RBAC)', () => {
  const roles: Rol[] = ['admin', 'owner', 'arquitecto', 'aprobador', 'member', 'viewer'];
  
  const rutasEsperadas = [
    '/', '/tickets', '/tickets/:numero', '/nuevo-ticket', 
    '/nueva-funcionalidad', '/aprobaciones', '/auditoria', 
    '/auditoria/:numero', '/conexiones', '/modelos', '/consumo'
  ];

  test('El mapa cubre exactamente las 11 rutas declaradas', () => {
    const rutasDeclaradas = Object.keys(MAPA_RUTAS_ROLES);
    expect(rutasDeclaradas.sort()).toEqual(rutasEsperadas.sort());
  });

  roles.forEach(rol => {
    describe(`Accesos para el rol: ${rol}`, () => {
      rutasEsperadas.forEach(ruta => {
        test(`Evalúa acceso de ${rol} a ${ruta}`, () => {
          const tieneAcceso = MAPA_RUTAS_ROLES[ruta].includes(rol);
          
          if (ruta === '/auditoria') {
            const deberiaTenerAcceso = ['admin', 'owner', 'arquitecto'].includes(rol);
            expect(tieneAcceso).toBe(deberiaTenerAcceso);
          }
          
          if (ruta === '/nuevo-ticket') {
            if (rol === 'viewer') expect(tieneAcceso).toBe(false);
          }

          if (['/conexiones', '/organizacion'].includes(ruta)) {
             const deberiaTenerAcceso = ['admin', 'owner'].includes(rol);
             expect(tieneAcceso).toBe(deberiaTenerAcceso);
          }
          
          expect(typeof tieneAcceso).toBe('boolean');
        });
      });
    });
  });
});