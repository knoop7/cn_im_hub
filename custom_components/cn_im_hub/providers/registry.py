"""Provider registry with auto-discovery."""

from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from pkgutil import iter_modules

from ..provider_flow import build_simple_provider_flow
from . import __path__ as PROVIDERS_PATH
from .base import ProviderSpec

_SKIP_MODULES = {"base", "registry", "wechat_flow"}


@lru_cache(maxsize=1)
def get_provider_specs() -> dict[str, ProviderSpec]:
    """Discover all provider specs from provider modules."""

    specs: dict[str, ProviderSpec] = {}
    for module_info in iter_modules(PROVIDERS_PATH):
        name = module_info.name
        if name.startswith("_") or name in _SKIP_MODULES:
            continue
        module = import_module(f"{__package__}.{name}")
        spec = getattr(module, "PROVIDER_SPEC", None)
        if spec is None:
            continue
        specs[spec.key] = spec
    return specs


def get_provider_spec(key: str) -> ProviderSpec:
    """Return a single provider spec."""

    return get_provider_specs()[key]


@lru_cache(maxsize=1)
def get_provider_flow_handlers() -> dict[str, type]:
    """Return subentry flow handlers for all providers."""

    handlers: dict[str, type] = {}
    for key, spec in get_provider_specs().items():
        handler = spec.flow_handler or build_simple_provider_flow(spec)
        setattr(handler, "_provider_spec", spec)
        handlers[key] = handler
    return handlers
