package com.acme.informes;

import org.springframework.jdbc.core.JdbcTemplate;

public class InformeRepository {
    private final JdbcTemplate jdbc;

    public InformeRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public int pagosDelMes() {
        return jdbc.queryForObject(
            "SELECT count(*) FROM billing.payments p WHERE p.created_at >= date_trunc('month', now())",
            Integer.class);
    }

    public void ajustarIva(java.math.BigDecimal tasa) {
        jdbc.update("UPDATE billing.tax_rates SET rate = ? WHERE code = 'IVA'", tasa);
    }

    public java.util.List<java.util.Map<String, Object>> almacenes() {
        String sql = """
            SELECT w.code, w.name
              FROM inventory.warehouses w
             ORDER BY w.code
            """;
        return jdbc.queryForList(sql);
    }

    public void limpiarDirecciones() {
        jdbc.update("DELETE FROM app.addresses a USING app.customers c "
            + "WHERE a.customer_id = c.id AND c.deleted_at IS NOT NULL");
    }
}
