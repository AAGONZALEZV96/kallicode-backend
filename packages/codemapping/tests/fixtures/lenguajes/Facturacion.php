<?php
namespace Acme\Facturacion;

use Acme\Util\Calculadora;
use Psr\Log\LoggerInterface;

interface Repositorio
{
    public function buscar(int $id): ?Factura;
}

trait Auditable
{
    public function auditar(string $evento): void
    {
        registrar_evento($evento);
    }
}

enum Estado: string
{
    case Pendiente = 'pendiente';
    case Pagada = 'pagada';
}

class Servicio extends Base implements Repositorio
{
    public function buscar(int $id): ?Factura
    {
        $f = $this->repo->buscar($id);
        $this->validar($f);
        return Calculadora::total($f) ?? parent::buscar($id);
    }

    private function validar(Factura $f): void
    {
        $cb = function () { return llamadaInterna(); };
        verificar($f);
    }
}

function aplicar_iva(float $neto): float
{
    return round($neto * 1.19, 2);
}
