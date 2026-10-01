"""Cálculo del porcentaje de completitud de una lista.

Función pura (sin I/O) reutilizada por los casos de uso para no duplicar la fórmula.
El conteo `(total, done)` se obtiene de una agregación en la BD (ver
`ITaskRepository.completion_stats`).
"""


def completion_percentage(total: int, done: int) -> float:
    """Porcentaje de tareas DONE sobre el total (2 decimales). Sin tareas → ``0.0``."""
    if total == 0:
        return 0.0
    return round(done / total * 100, 2)
