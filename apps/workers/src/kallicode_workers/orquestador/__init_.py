"""Orquestador del pipeline KalliCode.

Sprint 4 (KC-BE-04-1): núcleo del bucle + etapa de triage.
Sprint 6 (KC-BE-06-1/2): etapas de design, build, QA, seguridad y deploy.
"""

from .bucle import ciclo
from .cliente_interno import ClienteInterno
from .politica import Decision, decidir

__all__ = ["ClienteInterno", "Decision", "ciclo", "decidir"]
