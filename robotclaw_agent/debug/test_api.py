import os
import sys

from openai import OpenAI

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("未检测到 OPENAI_API_KEY 环境变量，请设置后重试。")
    sys.exit(1)

client = OpenAI(api_key=api_key)

try:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=5,
    )

    print("✅ API Key 有效，调用成功！")
    print("返回结果：", response.choices[0].message.content.strip())

except Exception as e:
    print("❌ API 调用失败：", str(e))
