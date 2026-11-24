/**
 * Política de contraseñas del backend (seguridad.validar_politica_password, KC-PT-10-6): de 12
 * a 128 caracteres, una mayúscula, una minúscula y un dígito. El portal la revisa al escribir;
 * el análisis de entropía (zxcvbn) solo lo hace el backend y llega como AUTH_PASSWORD_DEBIL.
 */
export const REQUISITOS_CLAVE = [
  { clave: "largo", cumple: (v: string) => v.length >= 12 && v.length <= 128 },
  { clave: "mayuscula", cumple: (v: string) => /\p{Lu}/u.test(v) },
  { clave: "minuscula", cumple: (v: string) => /\p{Ll}/u.test(v) },
  { clave: "digito", cumple: (v: string) => /\p{Nd}/u.test(v) },
] as const;

export const claveCumple = (valor: string) => REQUISITOS_CLAVE.every((r) => r.cumple(valor));
