"""Tests de `resolve_client_ip`: honra `X-Forwarded-For` solo desde proxies de confianza."""

from starlette.requests import Request

from app.web.client_ip import parse_trusted_proxies, resolve_client_ip


def _request(peer: str, *, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    scope = {"type": "http", "client": (peer, 12345), "headers": headers}
    return Request(scope)


def test_ignores_xff_when_no_trusted_proxies() -> None:
    # Sin proxies de confianza, XFF se ignora: se usa la IP del socket (anti-suplantación).
    req = _request("203.0.113.7", xff="1.2.3.4")
    assert resolve_client_ip(req, []) == "203.0.113.7"


def test_ignores_xff_when_peer_not_trusted() -> None:
    trusted = parse_trusted_proxies("10.0.0.0/8")
    req = _request("203.0.113.7", xff="1.2.3.4")  # peer público, no confiable
    assert resolve_client_ip(req, trusted) == "203.0.113.7"


def test_uses_xff_client_when_peer_is_trusted() -> None:
    trusted = parse_trusted_proxies("10.0.0.1")
    req = _request("10.0.0.1", xff="198.51.100.23")
    assert resolve_client_ip(req, trusted) == "198.51.100.23"


def test_peels_trusted_proxies_from_the_right() -> None:
    # Cadena: cliente real, proxy externo no confiable, dos proxies internos de confianza.
    trusted = parse_trusted_proxies("10.0.0.0/8")
    req = _request("10.0.0.2", xff="198.51.100.23, 203.0.113.9, 10.0.0.1")
    assert resolve_client_ip(req, trusted) == "203.0.113.9"


def test_all_trusted_returns_leftmost() -> None:
    trusted = parse_trusted_proxies("10.0.0.0/8")
    req = _request("10.0.0.2", xff="10.0.0.5, 10.0.0.1")
    assert resolve_client_ip(req, trusted) == "10.0.0.5"


def test_trusted_peer_without_xff_uses_peer() -> None:
    trusted = parse_trusted_proxies("10.0.0.1")
    assert resolve_client_ip(_request("10.0.0.1"), trusted) == "10.0.0.1"


def test_parse_ignores_blank_and_malformed_entries() -> None:
    trusted = parse_trusted_proxies(" 10.0.0.0/8 , , not-an-ip , 192.168.1.1 ")
    assert len(trusted) == 2
