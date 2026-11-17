const db = require("./db");

async function resumen() {
  const ventas = await db.query("SELECT count(*) FROM orders WHERE created_at > now() - interval '1 day'");
  const altas = await db.query("select count(*) from users where created_at > now() - interval '1 day'");
  const stock = await db.query("SELECT sum(quantity) FROM order_items");
  return { ventas, altas, stock };
}

function categoriasSql() {
  return "SELECT id, name FROM categories ORDER BY name";
}

async function auditar(tabla, id) {
  // Tabla dinámica: no hay acceso conocido que esperar.
  return db.query("INSERT INTO " + tabla + " (ref) VALUES ($1)", [id]);
}

async function moverStock(origen, destino) {
  await db.query(
    "UPDATE inventory.stock_movements SET warehouse_id = $2 WHERE warehouse_id = $1",
    [origen, destino],
  );
}

module.exports = { resumen, categoriasSql, auditar, moverStock };
