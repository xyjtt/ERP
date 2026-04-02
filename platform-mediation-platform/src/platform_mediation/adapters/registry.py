from __future__ import annotations

from platform_mediation.adapters.base import PlatformAdapter


class AdapterNotFoundError(KeyError):
    """Raised when no adapter matches the requested target."""


class AdapterRegistry:
    def __init__(self, adapters: list[PlatformAdapter] | None = None) -> None:
        self._adapters: dict[tuple[str, str, str], PlatformAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: PlatformAdapter) -> None:
        key = (adapter.platform, adapter.executor_type, adapter.adapter_code)
        self._adapters[key] = adapter

    def resolve(self, platform: str, executor_type: str, adapter_code: str | None = None) -> PlatformAdapter:
        adapter_key = adapter_code or f"{platform}_adapter"
        key = (platform, executor_type, adapter_key)
        adapter = self._adapters.get(key)
        if adapter is None:
            raise AdapterNotFoundError(f"adapter not found for {platform}/{executor_type}/{adapter_key}")
        return adapter

    def capability_summary(self) -> list[dict[str, object]]:
        return [adapter.capability() for adapter in self._adapters.values()]

