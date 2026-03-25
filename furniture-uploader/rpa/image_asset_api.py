from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_IMAGE_API_BASE_URL = "https://sc.jiansun.vip/api/external"
DEFAULT_IMAGE_API_KEY_ENV = "AI_IMAGE_API_KEY"


class ImageAssetApiError(RuntimeError):
    """Raised when the remote image asset API returns an invalid response."""


@dataclass
class ImageAssetBundle:
    resource_type: str
    source_spu: str
    source_sku: str
    candidate_id: str = ""
    main_urls: list[str] = field(default_factory=list)
    sku_urls: list[str] = field(default_factory=list)
    detail_urls: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class AIImageAssetClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_IMAGE_API_BASE_URL,
        api_key: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_runtime_config(cls, runtime_config: dict[str, Any]) -> "AIImageAssetClient":
        image_api_config = dict(runtime_config.get("image_api", {}))
        api_key = str(image_api_config.get("api_key", "")).strip()
        api_key_env = str(image_api_config.get("api_key_env", DEFAULT_IMAGE_API_KEY_ENV)).strip()
        if not api_key and api_key_env:
            api_key = os.environ.get(api_key_env, "").strip()
        base_url = str(image_api_config.get("base_url", DEFAULT_IMAGE_API_BASE_URL)).strip() or DEFAULT_IMAGE_API_BASE_URL
        timeout_seconds = float(image_api_config.get("timeout_seconds", 30))
        return cls(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def fetch_bundle(
        self,
        *,
        resource_type: str,
        sku: str = "",
        spu: str = "",
    ) -> ImageAssetBundle:
        if not self.is_configured():
            raise ImageAssetApiError("Image asset API key is not configured.")
        if not sku and not spu:
            raise ImageAssetApiError("Either sku or spu must be provided when fetching image assets.")

        params: dict[str, str] = {"resourceType": resource_type}
        if sku:
            params["sku"] = sku
        if spu:
            params["spu"] = spu

        payload = self._request_json("/standard-model-white-background", params)
        if resource_type == "task-detail":
            return self._build_task_detail_bundle(payload)
        if resource_type == "white-background":
            return self._build_white_background_bundle(payload)
        raise ImageAssetApiError(f"Unsupported image resource type: {resource_type}")

    def download_urls(
        self,
        urls: list[str],
        *,
        target_dir: Path,
        prefix: str,
    ) -> list[str]:
        target_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[str] = []
        for index, url in enumerate(urls, start=1):
            if not str(url).strip():
                continue
            target_path = target_dir / f"{prefix}_{index:02d}{self._guess_extension(url)}"
            self._download_file(str(url).strip(), target_path)
            saved_paths.append(str(target_path))
        return saved_paths

    def _request_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode({key: value for key, value in params.items() if str(value).strip()})
        request = Request(
            f"{self.base_url}{path}?{query}",
            headers={"X-API-Key": self.api_key},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {}
            error = payload.get("error", {})
            code = str(error.get("code", "")).strip()
            message = str(error.get("message", "")).strip() or body
            error_text = f"HTTP {exc.code}"
            if code:
                error_text = f"{error_text} {code}"
            if message:
                error_text = f"{error_text} {message}".strip()
            raise ImageAssetApiError(f"Image asset API request failed: {error_text}") from exc
        except URLError as exc:
            raise ImageAssetApiError(f"Image asset API request failed: {exc}") from exc
        payload = json.loads(body)
        if not payload.get("success", False):
            error = payload.get("error", {})
            raise ImageAssetApiError(
                f"Image asset API request failed: {error.get('code', 'UNKNOWN')} {error.get('message', '')}".strip()
            )
        return payload.get("data", {})

    def _build_task_detail_bundle(self, payload: dict[str, Any]) -> ImageAssetBundle:
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            raise ImageAssetApiError("task-detail response contains no candidates.")

        candidate = self._select_task_detail_candidate(candidates)
        download_urls = candidate.get("downloadUrls", {})
        main_urls = [str(item).strip() for item in download_urls.get("main", []) if str(item).strip()]
        sku_urls = [str(item).strip() for item in download_urls.get("sku", []) if str(item).strip()]
        detail_urls = [str(item).strip() for item in download_urls.get("detail", []) if str(item).strip()]

        return ImageAssetBundle(
            resource_type="task-detail",
            source_spu=str(payload.get("spu", "")).strip(),
            source_sku=str(payload.get("sku", "")).strip(),
            candidate_id=str(candidate.get("materialId", "")).strip(),
            main_urls=main_urls,
            sku_urls=sku_urls,
            detail_urls=detail_urls,
            metadata={
                "candidate_count": payload.get("candidateCount", len(candidates)),
                "task_no": str(candidate.get("taskNo", "")).strip(),
                "scene_name": str(candidate.get("sceneName", "")).strip(),
                "uploaded_at": str(candidate.get("uploadedAt", "")).strip(),
                "is_full_set": bool(candidate.get("isFullSet", False)),
            },
        )

    def _build_white_background_bundle(self, payload: dict[str, Any]) -> ImageAssetBundle:
        white_background = payload.get("whiteBackground", {})
        fixed_items = white_background.get("fixed", [])
        other_items = white_background.get("other", [])
        main_urls = [str(item.get("downloadUrl", "")).strip() for item in fixed_items if str(item.get("downloadUrl", "")).strip()]
        detail_urls = [str(item.get("downloadUrl", "")).strip() for item in other_items if str(item.get("downloadUrl", "")).strip()]

        if not main_urls:
            sku_list = payload.get("skuList", [])
            if isinstance(sku_list, list) and sku_list:
                selected = self._select_white_background_preview_candidate(sku_list)
                preview = dict(selected.get("previewImage") or {})
                preview_url = str(preview.get("downloadUrl", "")).strip() or str(preview.get("previewUrl", "")).strip()
                if preview_url:
                    return ImageAssetBundle(
                        resource_type="white-background",
                        source_spu=str(payload.get("spu", payload.get("productModel", ""))).strip(),
                        source_sku=str(selected.get("skuName", payload.get("sku", ""))).strip(),
                        candidate_id=str(payload.get("materialId", "")).strip(),
                        main_urls=[preview_url],
                        detail_urls=[],
                        metadata={
                            "white_background_complete": False,
                            "white_background_count": int(selected.get("whiteBackgroundCount", 0) or 0),
                            "preview_only": True,
                            "sku_count": int(payload.get("skuCount", len(sku_list)) or 0),
                            "selected_sku_name": str(selected.get("skuName", "")).strip(),
                        },
                    )

        return ImageAssetBundle(
            resource_type="white-background",
            source_spu=str(payload.get("productModel", "")).strip(),
            source_sku=str(payload.get("sku", "")).strip(),
            candidate_id=str(payload.get("materialId", "")).strip(),
            main_urls=main_urls,
            detail_urls=detail_urls,
            metadata={
                "white_background_complete": bool(payload.get("whiteBackgroundComplete", False)),
                "white_background_count": payload.get("whiteBackgroundCount", 0),
            },
        )

    def _select_white_background_preview_candidate(self, sku_list: list[dict[str, Any]]) -> dict[str, Any]:
        def rank_key(candidate: dict[str, Any]) -> tuple[int, int, str]:
            has_white_background = 1 if candidate.get("hasWhiteBackground") else 0
            white_background_count = int(candidate.get("whiteBackgroundCount", 0) or 0)
            sku_name = str(candidate.get("skuName", "")).strip()
            return (has_white_background, white_background_count, sku_name)

        return max(sku_list, key=rank_key)

    def _select_task_detail_candidate(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        def rank_key(candidate: dict[str, Any]) -> tuple[int, str]:
            is_full_set = 1 if candidate.get("isFullSet") else 0
            uploaded_at = str(candidate.get("uploadedAt", "")).strip()
            return (is_full_set, uploaded_at)

        return max(candidates, key=rank_key)

    def _download_file(self, url: str, target_path: Path) -> None:
        request = Request(url, method="GET")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = response.read()
        target_path.write_bytes(payload)

    def _guess_extension(self, url: str) -> str:
        lowered = url.lower()
        for extension in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            if extension in lowered:
                return extension
        return ".jpg"
