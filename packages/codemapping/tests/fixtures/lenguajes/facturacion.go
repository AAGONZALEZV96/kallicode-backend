package facturacion

import (
	"fmt"
	"strings"
)

type Servicio struct {
	repo Repositorio
}

type Repositorio interface {
	Buscar(id int) (*Factura, error)
	Guardar(f *Factura) error
}

type Estado int

func Nuevo(repo Repositorio) *Servicio {
	return &Servicio{repo: repo}
}

func (s *Servicio) Procesar(id int) error {
	f, err := s.repo.Buscar(id)
	if err != nil {
		return fmt.Errorf("buscar: %w", err)
	}
	total := calcularTotal(f)
	go func() { notificar(total) }()
	return s.repo.Guardar(f)
}

func calcularTotal(f *Factura) float64 {
	return aplicarIva(float64(len(strings.TrimSpace(f.Glosa))))
}

func aplicarIva(neto float64) float64 {
	return neto * 1.19
}
