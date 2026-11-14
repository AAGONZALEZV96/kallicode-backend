import { Pool } from "pg";

const pool = new Pool();

export async function stockBajo(minimo: number) {
  const { rows } = await pool.query(
    `SELECT s.name, sum(m.delta) AS stock
       FROM inventory.suppliers s
       JOIN inventory.stock_movements m ON m.supplier_id = s.id
      GROUP BY s.name
     HAVING sum(m.delta) < $1`,
    [minimo],
  );
  return rows;
}

export async function borrarSesionesCaducadas() {
  await pool.query("DELETE FROM sessions WHERE expires_at < now()");
}

export async function notificar(clienteId: string, texto: string) {
  await pool.query(
    'INSERT INTO app.notifications (customer_id, body) VALUES ($1, $2)',
    [clienteId, texto],
  );
}

export async function contar(nombre: string) {
  // Tabla dinámica: no hay acceso conocido que esperar.
  return pool.query(`SELECT count(*) FROM ${nombre}`);
}
