import unittest

from get_model_infer import _parse_json_object
from tools.web_search_baidu import extract_references, get_authority_level


class AdapterTests(unittest.TestCase):
    def test_parse_fenced_model_json(self):
        self.assertEqual(
            _parse_json_object('说明\n```json\n{"search_actions": []}\n```'),
            {"search_actions": []},
        )

    def test_authority_boundaries_and_reference_sort(self):
        self.assertEqual(get_authority_level(0.3), "中")
        self.assertEqual(get_authority_level(0.7), "中")
        data = {
            "references": [
                {
                    "id": 2,
                    "authority_score": 0.8,
                    "website": "B",
                    "date": "d2",
                    "title": "t2",
                    "content": "c2",
                },
                {
                    "id": 1,
                    "authority_score": 0.2,
                    "website": "A",
                    "date": "d1",
                    "title": "t1",
                    "content": "c1",
                },
            ]
        }
        result = extract_references(data)
        self.assertEqual([item["id"] for item in result], [1, 2])
        self.assertEqual(result[0]["authority_level"], "低")
        self.assertEqual(result[1]["authority_level"], "高")


if __name__ == "__main__":
    unittest.main()
