"""Fixtures E2E: cliente HTTP real contra el servidor realmente desplegado.

A diferencia de los tests de integración (que llaman a la app en memoria con
`ASGITransport`), estas pruebas golpean un servidor de verdad (uvicorn/Docker) sobre la
red, contra PostgreSQL real. Son opt-in (marcador `e2e`) y se excluyen de la corrida por
defecto. El servidor objetivo se toma de `E2E_BASE_URL` (def. http://localhost:8000).
"""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as http_client:
        # No se SALTA si el servidor no está disponible: estas pruebas son opt-in y se
        # excluyen por defecto, así que invocarlas significa "valida el despliegue". Si no
        # hay servidor es un FALLO real y explícito —no un skip que aparente éxito—, con un
        # mensaje accionable. En CI un paso previo espera la readiness antes de llegar aquí.
        try:
            health = await http_client.get("/health")
        except httpx.HTTPError as exc:
            pytest.fail(
                f"E2E requiere un servidor accesible en {BASE_URL}: {exc}. "
                "Levántalo con `docker compose up` (o ajusta E2E_BASE_URL).",
                pytrace=False,
            )
        if health.status_code != 200:
            pytest.fail(
                f"El servidor en {BASE_URL} respondió /health con {health.status_code}.",
                pytrace=False,
            )
        yield http_client
