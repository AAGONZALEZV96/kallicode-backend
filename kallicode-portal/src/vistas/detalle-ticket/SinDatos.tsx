/** Pestaña sin datos todavía (404 del backend): detalle-ticket.<zona>.sin-datos. */
export default function SinDatos({ zona }: { zona: string }) {
  return (
    <p className="note" data-testid={`detalle-ticket.${zona}.sin-datos`}>
      —
    </p>
  );
}
