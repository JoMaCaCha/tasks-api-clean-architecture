"""Tests del handler centralizado de excepciones de dominio."""

import pytest

from app.domain.exceptions import NotFoundError
from app.web.errors import domain_error_handler


async def test_handler_translates_domain_error() -> None:
    # Una DomainError se traduce a su código HTTP y mensaje. El handler no usa el
    # `request`, así que un None basta para ejercer la lógica de traducción.
    response = await domain_error_handler(None, NotFoundError("no existe"))
    assert response.status_code == 404


async def test_handler_reraises_non_domain_error() -> None:
    # El guard defensivo: una excepción ajena se vuelve a lanzar (no se traga ni se
    # convierte en un 400 silencioso), de modo que aflore como 500 real.
    with pytest.raises(ValueError):
        await domain_error_handler(None, ValueError("inesperado"))
