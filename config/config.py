import os

# ==========================================
# llm api key
# ==========================================
ds_api_key = os.environ.get("ds_api_key")

# ==========================================
# search tool api key
# ==========================================
baidu_api_key = os.environ.get("baidu_search_key1")
bocha_api_key = os.environ.get("bocha_search_key1")

# Search backend: "baidu" or "bocha". The environment variable is optional;
# this value can also be edited directly for local experiments.
search_engine = "bocha"

# ==========================================
# orchestrator config
# ==========================================
search_max_turns = 10
search_candidates_top_k = 5
max_queries_per_action = 5
max_web_content_chars = 6000
model_max_attempts = 3
model_retry_delay_seconds = 1.0
