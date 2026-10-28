package com.acme.billing;

import java.util.List;
import java.util.function.Function;

public class InvoiceService {

    public interface Notifier {
        void notify(String invoiceId);
    }

    public enum Status {
        DRAFT, ISSUED, VOID;

        public boolean isFinal() {
            return this != DRAFT;
        }
    }

    public static class Totals {
        private final long net;

        public Totals(long net) {
            this.net = net;
        }

        public long gross() {
            return Math.round(net * 1.19);
        }
    }

    private class AuditTrail {
        void record(String event) {
            System.out.println(event);
        }
    }

    public Runnable scheduleReminder(String invoiceId, Notifier notifier) {
        return new Runnable() {
            @Override
            public void run() {
                notifier.notify(invoiceId);
            }
        };
    }

    public <T> List<T> mapAll(List<String> ids, Function<String, T> mapper) {
        return ids.stream().map(id -> mapper.apply(id.trim())).toList();
    }
}
