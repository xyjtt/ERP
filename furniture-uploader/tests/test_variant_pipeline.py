from __future__ import annotations

import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from image_asset_api import AIImageAssetClient, ImageAssetApiError, ImageAssetBundle
from main import load_runtime_products, resolve_input_mode
from variant_pipeline import (
    _download_public_urls,
    _prepare_1688_square_main_images,
    _prepare_download_url,
    build_products_from_variants,
    load_release_variants,
)


class FakeImageClient:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str, str]] = []

    def fetch_bundle(self, *, resource_type: str, sku: str = "", spu: str = "") -> ImageAssetBundle:
        self.requested.append((resource_type, sku, spu))
        return ImageAssetBundle(
            resource_type=resource_type,
            source_spu=spu,
            source_sku=sku,
            candidate_id="material-2",
            main_urls=["https://example.com/main-1.jpg"],
            detail_urls=["https://example.com/detail-1.jpg", "https://example.com/detail-2.jpg"],
        )

    def download_urls(self, urls: list[str], *, target_dir: Path, prefix: str) -> list[str]:
        target_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for index, _url in enumerate(urls, start=1):
            target = target_dir / f"{prefix}_{index:02d}.jpg"
            Image.new("RGB", (4, 6), (200, 20, 20)).save(target, format="JPEG")
            saved.append(str(target))
        return saved


class FailingImageClient:
    def fetch_bundle(self, *, resource_type: str, sku: str = "", spu: str = "") -> ImageAssetBundle:
        raise ImageAssetApiError(f"image lookup failed for {resource_type}:{sku or spu}")

    def download_urls(self, urls: list[str], *, target_dir: Path, prefix: str) -> list[str]:
        raise RuntimeError("download should not be called")


class VariantPipelineTests(unittest.TestCase):
    def test_prepare_download_url_encodes_unicode_path_without_double_encoding(self) -> None:
        raw_url = "https://s3.example.com/由壹点AI/public-display/图片 01.webp?名称=新品%20图"

        prepared = _prepare_download_url(raw_url)

        self.assertIn("%E7%94%B1%E5%A3%B9%E7%82%B9AI", prepared)
        self.assertIn("%E5%9B%BE", prepared)
        self.assertIn("%20", prepared)
        self.assertNotIn("由壹点", prepared)
        self.assertNotIn("%2520", prepared)

    @patch("variant_pipeline.time.sleep")
    @patch("variant_pipeline.urlopen")
    def test_public_image_download_retries_transient_502(self, urlopen_mock, sleep_mock) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"image-bytes"
        urlopen_mock.side_effect = [
            HTTPError("https://example.invalid/a.jpg", 502, "bad gateway", None, None),
            response,
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _download_public_urls(
                ["https://example.invalid/a.jpg"],
                target_dir=Path(temp_dir),
                prefix="main",
            )
            self.assertEqual(Path(paths[0]).read_bytes(), b"image-bytes")
            self.assertTrue(Path(paths[0] + ".url").is_file())
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once_with(1)

    @patch("variant_pipeline.urlopen")
    def test_public_image_download_reuses_matching_cached_file(self, urlopen_mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "detail_01.jpg"
            target.write_bytes(b"cached")
            Path(str(target) + ".url").write_text(
                "https://example.invalid/a.jpg\n", encoding="utf-8"
            )
            paths = _download_public_urls(
                ["https://example.invalid/a.jpg"],
                target_dir=Path(temp_dir),
                prefix="detail",
            )
        self.assertEqual(paths, [str(target)])
        urlopen_mock.assert_not_called()

    @patch("variant_pipeline.urlopen")
    def test_public_webp_image_is_transcoded_to_jpeg(self, urlopen_mock) -> None:
        source = BytesIO()
        Image.new("RGBA", (2, 2), (255, 0, 0, 128)).save(source, format="WEBP")
        response = MagicMock()
        response.__enter__.return_value.read.return_value = source.getvalue()
        urlopen_mock.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _download_public_urls(
                ["https://example.invalid/a.webp"],
                target_dir=Path(temp_dir),
                prefix="main",
            )
            target = Path(paths[0])
            self.assertEqual(target.suffix, ".jpg")
            with Image.open(target) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.mode, "RGB")

    def test_load_release_variants_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "variants.json"
            path.write_text(
                """
                [
                  {
                    "variant_id": "V1",
                    "source_product_id": "SPU001",
                    "platform": "1688",
                    "shop_name": "测试店",
                    "title": "测试标题",
                    "price_value": "299",
                    "platform_category": "living_room",
                    "attributes": {"材质": "人造板"}
                  }
                ]
                """,
                encoding="utf-8",
            )

            variants = load_release_variants(path)

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].variant_id, "V1")
        self.assertEqual(variants[0].attributes["材质"], "人造板")

    def test_load_release_variants_infers_dimensions_from_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "variants.json"
            path.write_text(
                """
                [
                  {
                    "variant_id": "V1-DIM",
                    "source_product_id": "SPU001",
                    "platform": "1688",
                    "shop_name": "测试店",
                    "title": "测试标题",
                    "price_value": "299",
                    "platform_category": "living_room",
                    "attributes": {"size": "50x60x70cm"}
                  }
                ]
                """,
                encoding="utf-8",
            )
            variants = load_release_variants(path)

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].length_cm, "50")
        self.assertEqual(variants[0].width_cm, "60")
        self.assertEqual(variants[0].height_cm, "70")

    def test_load_release_variants_prefers_explicit_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "variants.json"
            path.write_text(
                """
                [
                  {
                    "variant_id": "V1-DIM-EXPLICIT",
                    "source_product_id": "SPU001",
                    "platform": "1688",
                    "shop_name": "测试店",
                    "title": "测试标题",
                    "price_value": "299",
                    "platform_category": "living_room",
                    "length_cm": "120",
                    "width_cm": "50",
                    "height_cm": "40",
                    "attributes": {"size": "10x20x30cm"}
                  }
                ]
                """,
                encoding="utf-8",
            )
            variants = load_release_variants(path)

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].length_cm, "120")
        self.assertEqual(variants[0].width_cm, "50")
        self.assertEqual(variants[0].height_cm, "40")

    def test_load_release_variants_supports_utf8_bom_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "variants.json"
            path.write_text(
                """
                [
                  {
                    "variant_id": "V1-BOM",
                    "source_product_id": "SPU001",
                    "platform": "1688",
                    "shop_name": "测试店",
                    "title": "测试标题",
                    "price_value": "299",
                    "platform_category": "living_room"
                  }
                ]
                """,
                encoding="utf-8-sig",
            )
            variants = load_release_variants(path)

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].variant_id, "V1-BOM")

    def test_build_products_from_variants_enriches_remote_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "variants.json"
            path.write_text(
                """
                [
                  {
                    "variant_id": "V2",
                    "source_product_id": "SPU002",
                    "platform": "1688",
                    "channel": "1688",
                    "shop_name": "测试店",
                    "title": "测试标题2",
                    "price_value": "399",
                    "platform_category": "living_room",
                    "image_source_type": "task-detail",
                    "image_source_spu": "SPU002",
                    "image_source_sku": "SKU002"
                  }
                ]
                """,
                encoding="utf-8",
            )
            variants = load_release_variants(path)
            products = build_products_from_variants(
                variants,
                image_client=FakeImageClient(),
                project_root=Path(temp_dir),
            )

        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].raw["main_image"].endswith("main_01_square.jpg"))
        self.assertIn("detail_01.jpg", products[0].raw["detail_images"])
        self.assertEqual(products[0].raw["main_image_remote"], "https://example.com/main-1.jpg")
        self.assertEqual(
            products[0].raw["detail_images_remote"],
            "https://example.com/detail-1.jpg|https://example.com/detail-2.jpg",
        )

    def test_prepare_1688_main_image_adds_white_square_canvas_without_cropping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            Image.new("RGB", (20, 40), (220, 20, 20)).save(source_path, format="PNG")

            prepared = _prepare_1688_square_main_images(
                [str(source_path)],
                target_dir=root / "prepared",
            )

            with Image.open(source_path) as source_image:
                self.assertEqual(source_image.size, (20, 40))
            with Image.open(prepared[0]) as prepared_image:
                self.assertEqual(prepared_image.size, (40, 40))
                self.assertEqual(prepared_image.mode, "RGB")
                self.assertGreater(sum(prepared_image.getpixel((0, 20))), 680)
                self.assertGreater(prepared_image.getpixel((20, 20))[0], 180)

    def test_image_client_uses_runtime_config(self) -> None:
        client = AIImageAssetClient.from_runtime_config(
            {
                "image_api": {
                    "api_key": "demo-key",
                    "base_url": "https://example.com/api",
                }
            }
        )

        self.assertTrue(client.is_configured())
        self.assertEqual(client.base_url, "https://example.com/api")

    def test_white_background_spu_payload_uses_preview_image(self) -> None:
        client = AIImageAssetClient(api_key="demo-key")
        bundle = client._build_white_background_bundle(
            {
                "spu": "CTG0149",
                "materialId": "material-1",
                "skuCount": 2,
                "skuList": [
                    {
                        "skuName": "CTG014913",
                        "hasWhiteBackground": True,
                        "whiteBackgroundCount": 8,
                        "previewImage": {
                            "downloadUrl": "https://example.com/preview-main.png"
                        },
                    }
                ],
            }
        )

        self.assertEqual(bundle.source_spu, "CTG0149")
        self.assertEqual(bundle.source_sku, "CTG014913")
        self.assertEqual(bundle.main_urls, ["https://example.com/preview-main.png"])
        self.assertTrue(bundle.metadata["preview_only"])

    def test_resolve_input_mode_auto_prefers_variant_for_json(self) -> None:
        self.assertEqual(resolve_input_mode(Path("demo.json"), "auto"), "variant")
        self.assertEqual(resolve_input_mode(Path("demo.csv"), "auto"), "product")

    def test_load_runtime_products_supports_variant_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "variants.json"
            path.write_text(
                """
                [
                  {
                    "variant_id": "V3",
                    "source_product_id": "SPU003",
                    "platform": "1688",
                    "channel": "1688",
                    "shop_name": "测试店",
                    "title": "测试标题3",
                    "price_value": "499",
                    "platform_category": "living_room",
                    "main_images": ["C:/images/main.jpg"],
                    "detail_images": ["C:/images/detail.jpg"]
                  }
                ]
                """,
                encoding="utf-8",
            )

            products = load_runtime_products(
                template_path=path,
                input_mode="variant",
                operator_config={"runtime": {}},
                project_root=Path(temp_dir),
            )

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].title, "测试标题3")
        self.assertEqual(products[0].raw["main_image"], "C:\\images\\main.jpg")

    def test_build_products_from_variants_keeps_validation_flow_when_image_lookup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "variants.json"
            path.write_text(
                """
                [
                  {
                    "variant_id": "V4",
                    "source_product_id": "SPU004",
                    "platform": "1688",
                    "channel": "1688",
                    "shop_name": "test-shop",
                    "title": "test-title-4",
                    "price_value": "599",
                    "platform_category": "living_room",
                    "image_source_type": "task-detail",
                    "image_source_spu": "SPU004"
                  }
                ]
                """,
                encoding="utf-8",
            )
            variants = load_release_variants(path)
            products = build_products_from_variants(
                variants,
                image_client=FailingImageClient(),
                project_root=Path(temp_dir),
            )

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].raw["main_image"], "")
        self.assertEqual(products[0].raw["main_image_remote"], "")
        self.assertIn("image lookup failed", products[0].raw["image_enrichment_error"])


if __name__ == "__main__":
    unittest.main()
