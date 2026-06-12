"""Tests del soporte de ETag/If-Match (parseo de la precondición de escritura)."""

import pytest

from app.domain.exceptions import PreconditionFailedError, PreconditionRequiredError
from app.web.etag import make_etag, require_if_match


def test_make_etag_is_strong_and_quoted() -> None:
    assert make_etag(7) == '"7"'


def test_require_if_match_parses_version() -> None:
    assert require_if_match('"3"') == 3
    assert require_if_match("3") == 3  # tolera el valor sin comillas


def test_wildcard_returns_none() -> None:
    assert require_if_match("*") is None


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_missing_if_match_raises_428(missing: str | None) -> None:
    with pytest.raises(PreconditionRequiredError):
        require_if_match(missing)


def test_weak_etag_is_rejected_412() -> None:
    # `If-Match` exige comparación fuerte (RFC 9110): un ETag débil no es válido aquí.
    with pytest.raises(PreconditionFailedError):
        require_if_match('W/"3"')


def test_malformed_etag_raises_412() -> None:
    with pytest.raises(PreconditionFailedError):
        require_if_match('"no-numerico"')
