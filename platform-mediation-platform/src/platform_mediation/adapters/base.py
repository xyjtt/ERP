from __future__ import annotations

from abc import ABC, abstractmethod

from platform_mediation.domain.models import AdapterExecutionRequest, AdapterExecutionResult


class PlatformAdapter(ABC):
    adapter_code: str
    platform: str
    executor_type: str
    version: str

    @abstractmethod
    def execute(self, request: AdapterExecutionRequest) -> AdapterExecutionResult: ...

    @abstractmethod
    def capability(self) -> dict[str, object]: ...

