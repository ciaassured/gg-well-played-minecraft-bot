from __future__ import annotations

import pytest

from jump_trainer.endpoints import expand_endpoint_template, parse_endpoint, resolve_endpoints


def test_repeatable_and_template_endpoints_are_ordered() -> None:
    explicit = resolve_endpoints(endpoint_values=["client-b:2", "client-a:1"])
    assert [endpoint.address for endpoint in explicit] == ["client-b:2", "client-a:1"]
    expanded = expand_endpoint_template("jump-client-{index}.jump-clients:64123", 117)
    assert len(expanded) == 117
    assert expanded[17].ordinal == 17
    assert expanded[-1].address == "jump-client-116.jump-clients:64123"


@pytest.mark.parametrize(
    "value",
    ("", "host", "host:0", "host:65536", "host:not-a-port", "unbracketed:v6:123"),
)
def test_invalid_endpoints_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_endpoint(value)


def test_endpoint_modes_cannot_be_mixed_or_duplicated() -> None:
    with pytest.raises(ValueError, match="mixed"):
        resolve_endpoints(endpoint_values=["a:1"], host="localhost")
    with pytest.raises(ValueError, match="duplicate"):
        resolve_endpoints(endpoint_values=["a:1", "a:1"])
    with pytest.raises(ValueError, match="together"):
        resolve_endpoints(endpoint_template="client-{index}:1")
    with pytest.raises(ValueError, match=r"exactly one \{index\}"):
        expand_endpoint_template("client:64123", 2)


def test_single_client_fallback_and_ipv6() -> None:
    assert resolve_endpoints()[0].address == "127.0.0.1:64123"
    assert parse_endpoint("[::1]:64123").address == "[::1]:64123"
    assert resolve_endpoints(host="::1")[0].address == "[::1]:64123"
