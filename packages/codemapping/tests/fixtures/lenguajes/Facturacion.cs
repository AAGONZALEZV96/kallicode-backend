using System;
using System.Collections.Generic;

namespace Acme.Facturacion
{
    public interface IRepositorio
    {
        Factura Buscar(int id);
    }

    public enum Estado { Pendiente, Pagada }

    public struct Monto
    {
        public decimal Valor;
    }

    public record Cliente(int Id, string Nombre);

    public class ServicioFacturas : IRepositorio
    {
        private readonly IRepositorio repo;

        public ServicioFacturas(IRepositorio repo)
        {
            this.repo = repo;
            Inicializar();
        }

        public Factura Buscar(int id)
        {
            var f = repo.Buscar(id);
            Validar(f);
            return Calculadora.Total<decimal>(f);
        }

        private static decimal Iva(decimal neto) => Redondear(neto * 0.19m);

        public void Procesar(IEnumerable<Factura> facturas)
        {
            foreach (var f in facturas)
            {
                Registrar(f);
            }
            Action<Factura> cb = x => { LlamadaInterna(x); };
            int Local(int n) { return Auxiliar(n); }
        }
    }
}
