from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

import agv_tender_scraper
import gpu_tender_scraper
import tender_core
from tender_core import ReportProfile, SiteConfig, TenderItem


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.com/") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, _url: str, timeout: int = 30) -> FakeResponse:
        return FakeResponse(self._text)


class TenderCoreTests(unittest.TestCase):
    def tearDown(self) -> None:
        tender_core.DISCOVERED_SEARCH_CACHE.clear()
        tender_core.DISCOVERY_FAILURE_LOGGED.clear()

    def test_agv_default_keyword_compatibility(self) -> None:
        parser = tender_core.build_parser(agv_tender_scraper.AGV_PROFILE)
        args = parser.parse_args([])
        self.assertEqual(tender_core.resolve_keywords(args, agv_tender_scraper.AGV_PROFILE), ["AGV"])
        self.assertEqual(args.sites, "all")

    def test_gpu_default_keywords(self) -> None:
        parser = tender_core.build_parser(gpu_tender_scraper.GPU_PROFILE)
        args = parser.parse_args([])
        self.assertEqual(
            tender_core.resolve_keywords(args, gpu_tender_scraper.GPU_PROFILE),
            [
                "GPU",
                "GPU服务器",
                "GPU算力",
                "算力服务",
                "GPU租赁",
                "算力租赁",
                "智算服务",
            ],
        )

    def test_dedupe_uses_url(self) -> None:
        items = [
            TenderItem("A", "title-1", tender_core.date(2025, 1, 1), "https://example.com/item"),
            TenderItem("B", "title-2", tender_core.date(2025, 1, 2), "https://example.com/item"),
            TenderItem("C", "title-3", tender_core.date(2025, 1, 3), "https://example.com/other"),
        ]
        deduped = tender_core.dedupe(items)
        self.assertEqual(len(deduped), 2)
        self.assertEqual([item.source for item in deduped], ["A", "C"])

    def test_report_title_is_parameterized(self) -> None:
        profile = ReportProfile(
            key="gpu",
            label="GPU",
            default_keywords=("GPU",),
            default_sites=("all",),
            default_subject_prefix="GPU 招标信息简报",
        )
        content = tender_core.build_email_content(
            [
                TenderItem(
                    source="测试站点",
                    title="GPU服务器采购",
                    publish_date=tender_core.date(2025, 1, 1),
                    url="https://example.com/gpu",
                )
            ],
            profile,
            ["GPU", "GPU服务器"],
            tender_core.date(2025, 1, 1),
            tender_core.date(2025, 1, 7),
        )
        self.assertIn("GPU 招标信息简报（关键词: GPU, GPU服务器）", content["text"])
        self.assertIn("GPU 招标信息简报（关键词: GPU, GPU服务器）", content["html"])

    def test_list_sites_contains_new_domestic_sites(self) -> None:
        sites = tender_core.list_sites()
        self.assertTrue(any(line.startswith("365trade:") for line in sites))
        self.assertTrue(any(line.startswith("ecsg:") for line in sites))
        self.assertTrue(any(line.startswith("ygcgfw:") for line in sites))

    @unittest.skipIf(
        tender_core.requests is None or tender_core.BeautifulSoup is None,
        "HTML parsing dependencies are not installed",
    )
    def test_discover_search_form_supports_get_and_post(self) -> None:
        get_site = SiteConfig(key="ecsg", name="ECSG", search_url="https://ecsg.com.cn/", auto_discover=True)
        post_site = SiteConfig(
            key="ygcgfw",
            name="YGCGFW",
            search_url="https://www.ygcgfw.com/",
            auto_discover=True,
        )

        get_html = (FIXTURES_DIR / "ecsg_home.html").read_text(encoding="utf-8")
        post_html = (FIXTURES_DIR / "ygcgfw_home.html").read_text(encoding="utf-8")

        get_result = tender_core.discover_search_form(FakeSession(get_html), get_site)
        post_result = tender_core.discover_search_form(FakeSession(post_html), post_site)

        self.assertIsNotNone(get_result)
        self.assertEqual(get_result.method, "get")
        self.assertEqual(get_result.keyword_param, "searchword")
        self.assertEqual(get_result.extra_params["channelCode"], "001")

        self.assertIsNotNone(post_result)
        self.assertEqual(post_result.method, "post")
        self.assertEqual(post_result.keyword_param, "keyword")
        self.assertEqual(post_result.extra_params["category"], "notice")

    @unittest.skipIf(
        tender_core.requests is None or tender_core.BeautifulSoup is None,
        "HTML parsing dependencies are not installed",
    )
    def test_parse_365trade_page(self) -> None:
        html_text = (FIXTURES_DIR / "365trade_search.html").read_text(encoding="utf-8")
        items, next_url = tender_core.parse_365trade_page(
            html_text,
            "GPU",
            tender_core.date(2025, 1, 1),
            tender_core.date(2025, 1, 7),
            "https://www.365trade.com.cn/search_156.jspx?q=GPU",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "GPU服务器采购项目招标公告")
        self.assertEqual(items[0].url, "https://www.365trade.com.cn/bid/123.html")
        self.assertEqual(next_url, "https://www.365trade.com.cn/search_156.jspx?page=2&q=GPU")

    @unittest.skipIf(tender_core.requests is None, "requests is not installed")
    def test_cebpubservice_fixture_shape(self) -> None:
        data = json.loads((FIXTURES_DIR / "cebpubservice_www.json").read_text(encoding="utf-8"))
        self.assertIn("object", data)
        self.assertIn("returnlist", data["object"])
        self.assertEqual(data["object"]["page"]["totalPage"], 1)

    def test_keywords_argument_overrides_single_keyword(self) -> None:
        parser = tender_core.build_parser(gpu_tender_scraper.GPU_PROFILE)
        args = parser.parse_args(["--keyword", "GPU", "--keywords", "GPU服务器,算力服务"])
        self.assertEqual(tender_core.resolve_keywords(args, gpu_tender_scraper.GPU_PROFILE), ["GPU服务器", "算力服务"])

    def test_subject_generation(self) -> None:
        subject = tender_core.build_subject(
            gpu_tender_scraper.GPU_PROFILE,
            "",
            tender_core.date(2025, 1, 7),
            3,
        )
        self.assertEqual(subject, "GPU 招标信息简报 2025-01-07（3条）")

    def test_title_keyword_matching_handles_spacing(self) -> None:
        self.assertTrue(tender_core.title_matches_keyword("GPU 服务器采购项目", "GPU服务器"))
        self.assertTrue(tender_core.title_matches_keyword("GPU-算力租赁服务", "GPU算力"))
        self.assertFalse(tender_core.title_matches_keyword("叉车采购项目", "GPU"))

    @patch("tender_core.discover_search_form", return_value=None)
    @patch("tender_core.create_session")
    def test_auto_discover_failure_skips_site(self, mock_create_session, _mock_discover) -> None:
        mock_create_session.return_value = object()
        site = SiteConfig(
            key="ecsg",
            name="南方电网电子采购交易平台",
            search_url="https://ecsg.com.cn/",
            auto_discover=True,
        )
        items = tender_core.fetch_generic_site(
            site,
            "GPU",
            tender_core.date(2025, 1, 1),
            tender_core.date(2025, 1, 7),
            3,
        )
        self.assertEqual(items, [])

    def test_proxy_env_default_is_disabled(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertFalse(tender_core.use_env_proxy())
        with patch.dict("os.environ", {"TENDER_USE_ENV_PROXY": "1"}, clear=False):
            self.assertTrue(tender_core.use_env_proxy())


if __name__ == "__main__":
    unittest.main()
