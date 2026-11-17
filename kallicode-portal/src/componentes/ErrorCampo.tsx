/**
 * Mensajes de error de los formularios (KC-PT-06-5). ErrorCampo va bajo su control y
 * se enlaza con aria-describedby (atributosDeError); ResumenErrores avisa al enviar que
 * hay campos por corregir y lista los errores de campos que el formulario no muestra.
 */
interface PropsCampo {
  id: string;
  error: string | undefined;
  testid: string;
}

export function ErrorCampo({ id, error, testid }: PropsCampo) {
  if (!error) return null;
  return (
    <p id={id} className="campo-error" data-testid={testid}>
      {error}
    </p>
  );
}

interface PropsResumen {
  mensaje: string;
  /** Errores sin un campo donde mostrarse. */
  sueltos?: string[];
  testid: string;
}

export function ResumenErrores({ mensaje, sueltos = [], testid }: PropsResumen) {
  return (
    <div className="error-box" role="alert" data-testid={testid}>
      {mensaje}
      {sueltos.length > 0 && (
        <ul className="errores-sueltos">
          {sueltos.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
