from __future__ import annotations

import pytest

from agmind.core.domain import validate_domain

pytestmark = pytest.mark.backend_any


def test_validate_domain_normalizes_case_and_trailing_dot() -> None:
    assert validate_domain("Lab.Example.COM.") == "lab.example.com"


@pytest.mark.parametrize(
    "domain",
    [
        "",
        "localhost",
        "bad domain.example",
        "bad`domain.example",
        "bad\n.example",
        "evil.${VAR}.example",
        "https://lab.example.com",
        "*.example.com",
        "-bad.example.com",
        "bad-.example.com",
    ],
)
def test_validate_domain_rejects_invalid_dns_names(domain: str) -> None:
    with pytest.raises(ValueError, match="domain"):
        validate_domain(domain)
