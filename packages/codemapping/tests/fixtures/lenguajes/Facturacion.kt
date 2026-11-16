package acme.facturacion

import kotlin.math.abs
import acme.util.Calculadora

interface Repositorio {
    fun buscar(id: Int): Factura?
}

enum class Estado { PENDIENTE, PAGADA }

class Servicio(private val repo: Repositorio) : Repositorio {
    override fun buscar(id: Int): Factura? {
        val f = repo.buscar(id)
        validar(f)
        return Calculadora.total(f) ?: abs(id).let { null }
    }

    private fun validar(f: Factura?) = verificar(f)

    fun procesar(ids: List<Int>) {
        ids.forEach { registrar(it) }
    }
}

object Utilidades {
    fun redondear(x: Double): Double = Math.round(x * 100) / 100.0
}

fun aplicarIva(neto: Double): Double = redondear(neto * 1.19)
