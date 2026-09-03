"""Strict client-pool endpoint parsing shared by every trainer command."""

from __future__ import annotations

import re
import string
from dataclasses import asdict, dataclass

from jump_trainer.config import DEFAULT_HOST, DEFAULT_PORT


@dataclass(frozen=True, order=True)
class Endpoint:
    index: int
    host: str
    port: int
    ordinal: int | None = None

    @property
    def address(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def as_dict(self) -> dict[str, int | str | None]:
        return asdict(self) | {"address": self.address}


def parse_endpoint(value: str, *, index: int = 0) -> Endpoint:
    """Parse HOST:PORT, including bracketed IPv6, without DNS resolution."""

    text = value.strip()
    if not text or any(character.isspace() for character in text):
        raise ValueError(f"invalid endpoint: {value!r}")
    if text.startswith("["):
        closing = text.find("]")
        if closing <= 1 or closing + 1 >= len(text) or text[closing + 1] != ":":
            raise ValueError(f"invalid bracketed endpoint: {value!r}")
        host, port_text = text[1:closing], text[closing + 2 :]
    else:
        if text.count(":") != 1:
            raise ValueError(f"endpoint must be HOST:PORT: {value!r}")
        host, port_text = text.rsplit(":", 1)
    if not host or not port_text.isdecimal():
        raise ValueError(f"endpoint must be HOST:PORT: {value!r}")
    port = int(port_text)
    if not 1 <= port <= 65_535:
        raise ValueError(f"endpoint port is outside 1..65535: {value!r}")
    match = re.search(r"-(\d+)(?:\.|$)", host)
    ordinal = int(match.group(1)) if match else None
    return Endpoint(index=index, host=host, port=port, ordinal=ordinal)


def expand_endpoint_template(template: str, clients: int) -> tuple[Endpoint, ...]:
    if clients <= 0:
        raise ValueError("client count must be positive")
    parsed = list(string.Formatter().parse(template))
    fields = [field for _literal, field, _spec, _conversion in parsed if field is not None]
    if fields != ["index"]:
        raise ValueError("endpoint template must contain exactly one {index} field")
    for _literal, field, spec, conversion in parsed:
        if field is not None and (spec or conversion):
            raise ValueError("endpoint template does not allow formatting options")
    try:
        rendered = tuple(template.format(index=index) for index in range(clients))
    except (IndexError, KeyError, ValueError) as exception:
        raise ValueError("invalid endpoint template") from exception
    return _unique(
        tuple(parse_endpoint(value, index=index) for index, value in enumerate(rendered))
    )


def resolve_endpoints(
    *,
    endpoint_values: list[str] | tuple[str, ...] | None = None,
    endpoint_template: str | None = None,
    clients: int | None = None,
    host: str | None = None,
    port: int | None = None,
) -> tuple[Endpoint, ...]:
    explicit = tuple(endpoint_values or ())
    pool_mode = bool(explicit) or endpoint_template is not None or clients is not None
    if pool_mode and (host is not None or port is not None):
        raise ValueError("--host/--port cannot be mixed with pool endpoint options")
    if explicit and (endpoint_template is not None or clients is not None):
        raise ValueError("--endpoint cannot be mixed with --endpoint-template/--clients")
    if (endpoint_template is None) != (clients is None):
        raise ValueError("--endpoint-template and --clients must be supplied together")
    if explicit:
        return _unique(
            tuple(parse_endpoint(value, index=index) for index, value in enumerate(explicit))
        )
    if endpoint_template is not None and clients is not None:
        return expand_endpoint_template(endpoint_template, clients)
    selected_host = DEFAULT_HOST if host is None else host
    selected_port = DEFAULT_PORT if port is None else port
    rendered_host = f"[{selected_host}]" if ":" in selected_host else selected_host
    return (parse_endpoint(f"{rendered_host}:{selected_port}"),)


def _unique(endpoints: tuple[Endpoint, ...]) -> tuple[Endpoint, ...]:
    addresses = [endpoint.address for endpoint in endpoints]
    if len(set(addresses)) != len(addresses):
        raise ValueError("duplicate client endpoints are not allowed")
    return endpoints
