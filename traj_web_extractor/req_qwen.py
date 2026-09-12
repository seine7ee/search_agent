import os

from openai import OpenAI

def req_qwen_model(messages):
    client = OpenAI(
        # api_key=os.environ.get("silicon_key"),
        api_key="sk-rjspoaleyflydkvbkgqlvphovcofwjyihxpycaoewiunghci",
        base_url="https://api.siliconflow.cn/v1"
    )

    completion = client.chat.completions.create(
        model="Qwen/Qwen3-8B",
        messages=messages,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": True}},
        stream=True
    )
    # print(response.choices[0].message.content)

    reasoning_content = ""  # 完整思考过程
    answer_content = ""  # 完整回复
    is_answering = False  # 是否进入回复阶段
    print("\n" + "=" * 20 + "思考过程" + "=" * 20 + "\n")

    for chunk in completion:
        if not chunk.choices:
            print("\nUsage:")
            print(chunk.usage)
            continue

        delta = chunk.choices[0].delta

        # 只收集思考内容
        if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
            if not is_answering:
                print(delta.reasoning_content, end="", flush=True)
            reasoning_content += delta.reasoning_content

        # 收到content，开始进行回复
        if hasattr(delta, "content") and delta.content:
            if not is_answering:
                print("\n" + "=" * 20 + "完整回复" + "=" * 20 + "\n")
                is_answering = True
            print(delta.content, end="", flush=True)
            answer_content += delta.content
    return answer_content