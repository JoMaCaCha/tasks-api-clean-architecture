"""Resolución de la IP real del cliente respetando *proxies* de confianza.

`X-Forwarded-For` es **falsificable**: un cliente puede enviarlo para suplantar otra IP y
evadir el límite por IP del login/registro. Por eso solo se honra cuando la conexión
entrante proviene de un proxy de confianza explícitamente configurado
(`RATE_LIMIT_TRUSTED_PROXIES`); en cualquier otro caso se usa la IP del socket. Es la guía
de FastAPI/Starlette y OWASP: no confiar en cabeceras forwarded salvo desde proxies
conocidos. Ver DECISION_LOG ADR-17.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from starlette.requests import Request

# Una red IPv4 o IPv6 (acepta IP suelta como /32 o /128 vía `strict=False`).
TrustedProxy = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_trusted_proxies(raw: str) -> list[TrustedProxy]:
    """Convierte una lista CSV de IPs/CIDR en redes. Entradas inválidas se ignoran."""
    networks: list[TrustedProxy] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            # Una entrada mal formada no debe tumbar el arranque; simplemente no se
            # confía en ella (degradación segura: menos confianza, no más).
            continue
    return networks


def _is_trusted(ip: str, trusted: Sequence[TrustedProxy]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in trusted)


def resolve_client_ip(request: Request, trusted: Sequence[TrustedProxy]) -> str:
    """IP del cliente para el límite por IP, resistente a suplantación de `XFF`.

    - Si no hay proxies de confianza, o el peer directo **no** es uno de ellos, se usa la IP
      del socket e **ignora** `X-Forwarded-For` (un atacante directo no puede falsearla).
    - Si el peer es de confianza, se recorre `X-Forwarded-For` de derecha a izquierda
      descartando los saltos de confianza; la primera IP no confiable es el cliente real
      (si todas son de confianza, se devuelve la más a la izquierda).
    """
    peer = request.client.host if request.client else "unknown"
    if not trusted or not _is_trusted(peer, trusted):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    for ip in reversed(chain):
        if not _is_trusted(ip, trusted):
            return ip
    return chain[0] if chain else peer
