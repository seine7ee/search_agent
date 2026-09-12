import contextlib
import io
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from traj_web_extractor import quote_config
from traj_web_extractor.quote_extractor import extract_goal_web_quotes
from traj_web_extractor import req_ds


def fake_openai_module(response):
    module = types.ModuleType("openai")
    client = Mock()
    client.chat.completions.create.return_value = response
    module.OpenAI = Mock(return_value=client)
    return module, client


class DeepSeekAdapterTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{"role": "user", "content": "extract"}]

    def test_standard_returns_raw_answer_text_without_json_object_parsing(self):
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='"片段1"｜"片段2"')
        )])
        openai_module, client = fake_openai_module(response)
        with patch.object(req_ds.cfg, "ds_api_key", "test-key"), \
             patch.dict(sys.modules, {"openai": openai_module}), \
             contextlib.redirect_stdout(io.StringIO()):
            result = req_ds.request_model_standard(self.messages)
        self.assertEqual(result, '"片段1"｜"片段2"')
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertIs(kwargs["stream"], False)
        self.assertNotIn("response_format", kwargs)

    def test_stream_returns_only_concatenated_answer_content(self):
        chunks = [
            SimpleNamespace(choices=[]),
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, reasoning_content="thinking")
            )]),
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="[3,", reasoning_content=None)
            )]),
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=" 5]", reasoning_content=None)
            )]),
        ]
        openai_module, client = fake_openai_module(chunks)
        with patch.object(req_ds.cfg, "ds_api_key", "test-key"), \
             patch.dict(sys.modules, {"openai": openai_module}), \
             contextlib.redirect_stdout(io.StringIO()):
            result = req_ds.request_model_stream(self.messages)
        self.assertEqual(result, "[3, 5]")
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertIs(kwargs["stream"], True)
        self.assertNotIn("response_format", kwargs)

    def test_request_model_dispatches_stream_and_standard(self):
        with patch.object(req_ds, "request_model_stream", return_value="stream") as stream, \
             patch.object(req_ds, "request_model_standard", return_value="standard") as standard:
            self.assertEqual(req_ds.request_model(self.messages), "stream")
            self.assertEqual(req_ds.request_model(self.messages, stream=False), "standard")
        stream.assert_called_once_with(self.messages)
        standard.assert_called_once_with(self.messages)


class ModelProviderRoutingTests(unittest.TestCase):
    def setUp(self):
        mode_patch = patch.object(quote_config, "DEFAULT_EXTRACTION_MODE", "verbatim")
        mode_patch.start()
        self.addCleanup(mode_patch.stop)
        self.data = {
            "user_query": "问题",
            "search_goals": [{
                "search_goal_id": "G1",
                "search_goal": "目标",
                "webs": [{"web_id": "W1", "web_content": "正文"}],
            }],
        }

    def test_qwen_provider_routes_to_qwen(self):
        qwen_module = types.ModuleType("traj_web_extractor.req_qwen")
        qwen_module.req_qwen_model = Mock(return_value='"正文"')
        ds_module = types.ModuleType("traj_web_extractor.req_ds")
        ds_module.request_model = Mock(return_value='"不应调用"')
        with patch.dict(sys.modules, {
            qwen_module.__name__: qwen_module,
            ds_module.__name__: ds_module,
        }):
            result = extract_goal_web_quotes(self.data, model_provider="qwen")
        self.assertEqual(result[0]["quotes"], ["正文"])
        qwen_module.req_qwen_model.assert_called_once()
        ds_module.request_model.assert_not_called()

    def test_ds_provider_routes_to_request_model(self):
        qwen_module = types.ModuleType("traj_web_extractor.req_qwen")
        qwen_module.req_qwen_model = Mock(return_value='"不应调用"')
        ds_module = types.ModuleType("traj_web_extractor.req_ds")
        ds_module.request_model = Mock(return_value='"正文"')
        with patch.dict(sys.modules, {
            qwen_module.__name__: qwen_module,
            ds_module.__name__: ds_module,
        }):
            result = extract_goal_web_quotes(self.data, model_provider="ds")
        self.assertEqual(result[0]["quotes"], ["正文"])
        ds_module.request_model.assert_called_once()
        qwen_module.req_qwen_model.assert_not_called()

    def test_ds_raw_array_is_parsed_by_sentence_id_strategy(self):
        self.data["search_goals"][0]["webs"][0]["web_content"] = "一。二。三。"
        ds_module = types.ModuleType("traj_web_extractor.req_ds")
        ds_module.request_model = Mock(return_value="[1, 3]")
        with patch.dict(sys.modules, {ds_module.__name__: ds_module}):
            result = extract_goal_web_quotes(
                self.data, extraction_mode="sentence_ids", model_provider="ds",
            )
        self.assertEqual(result[0]["sentence_ids"], [1, 3])
        self.assertEqual(result[0]["quotes"], ["一。二。三。"])

    def test_global_default_provider_can_be_changed(self):
        ds_module = types.ModuleType("traj_web_extractor.req_ds")
        ds_module.request_model = Mock(return_value='"正文"')
        with patch.object(quote_config, "DEFAULT_MODEL_PROVIDER", "ds"), \
             patch.dict(sys.modules, {ds_module.__name__: ds_module}):
            result = extract_goal_web_quotes(self.data)
        self.assertEqual(result[0]["quotes"], ["正文"])
        ds_module.request_model.assert_called_once()

    def test_invalid_provider_fails_before_model_request(self):
        model = Mock()
        with self.assertRaises(ValueError):
            extract_goal_web_quotes(
                self.data, request_model=model, model_provider="unknown",
            )
        model.assert_not_called()

    def test_injected_request_model_still_takes_precedence(self):
        injected = Mock(return_value='"正文"')
        ds_module = types.ModuleType("traj_web_extractor.req_ds")
        ds_module.request_model = Mock(side_effect=AssertionError("must not be called"))
        with patch.dict(sys.modules, {ds_module.__name__: ds_module}):
            result = extract_goal_web_quotes(
                self.data, request_model=injected, model_provider="ds",
            )
        self.assertEqual(result[0]["quotes"], ["正文"])
        injected.assert_called_once()
        ds_module.request_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
