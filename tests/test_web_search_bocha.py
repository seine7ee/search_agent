import unittest

from tools.web_search_bocha import convert_web_page_id, extract_web_pages


class BochaWebSearchTests(unittest.TestCase):
    def test_extract_filter_transform_and_preserve_source_id(self):
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {
                            "id": "https://api.bochaai.com/v1/#WebPages.0",
                            "name": "第一篇",
                            "url": "https://example.com/1",
                            "summary": "内容一",
                            "siteName": "网站一",
                            "datePublished": "2026-01-01T08:00:00+08:00",
                            "isNavigational": False,
                        },
                        {
                            "id": "https://api.bochaai.com/v1/#WebPages.1",
                            "name": "导航页",
                            "url": "https://example.com/navigation",
                            "summary": "应当剔除",
                            "siteName": "导航站",
                            "datePublished": None,
                            "isNavigational": True,
                        },
                        {
                            "id": "https://api.bochaai.com/v1/#WebPages.2",
                            "name": "第三篇",
                            "url": "https://example.com/3",
                            "summary": "内容三",
                            "siteName": "网站三",
                            "datePublished": None,
                            "isNavigational": None,
                        },
                    ]
                }
            },
        }

        result = extract_web_pages(payload)

        self.assertEqual([item["id"] for item in result], [1, 3])
        self.assertEqual(result[0]["title"], "第一篇")
        self.assertEqual(result[0]["content"], "内容一")
        self.assertEqual(result[0]["website"], "网站一")
        self.assertFalse(result[1]["isNavigational"])
        self.assertEqual(result[1]["date"], "")
        self.assertEqual(
            result[0]["web_content"],
            "网页来源: 网站一｜网页时间: 2026-01-01T08:00:00+08:00｜"
            "网页标题: 第一篇｜网页内容：内容一",
        )

    def test_id_conversion_and_fallback(self):
        self.assertEqual(
            convert_web_page_id("https://api.bochaai.com/v1/#WebPages.0", 9), 1
        )
        self.assertEqual(convert_web_page_id("unexpected-id", 4), 5)

    def test_rejects_missing_web_pages_value(self):
        with self.assertRaisesRegex(TypeError, r"data\.webPages\.value"):
            extract_web_pages({"data": {"webPages": {}}})


if __name__ == "__main__":
    unittest.main()
