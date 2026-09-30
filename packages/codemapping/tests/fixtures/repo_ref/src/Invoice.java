package com.kallicode.billing;

import java.math.BigDecimal;
import java.util.List;

public class Invoice {
    private final String id;
    private BigDecimal amount;
    private boolean paid;

    public Invoice(String id, BigDecimal amount) {
        this.id = id;
        this.amount = amount;
        this.paid = false;
    }

    public BigDecimal calculateTotal(BigDecimal taxRate) {
        return amount.multiply(BigDecimal.ONE.add(taxRate));
    }

    public void markAsPaid() {
        this.paid = true;
    }

    public static class LineItem {
        private String description;
        private BigDecimal price;

        public LineItem(String description, BigDecimal price) {
            this.description = description;
            this.price = price;
        }
    }
}