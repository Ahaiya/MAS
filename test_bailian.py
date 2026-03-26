import os
from dotenv import load_dotenv
from openai import OpenAI  # 百炼平台兼容 OpenAI 接口，直接用就行

# 1. 强制加载当前目录下的 .env 文件
load_dotenv()

# 2. 获取 Key 和 Base URL
# 百炼平台的 Key 存放在 RATER_2_API_KEY（对应 bundle 中 rater_2 的配置）
api_key = os.getenv("RATER_2_API_KEY")
base_url = os.getenv("RATER_2_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
model_name = os.getenv("RATER_2_MODEL", "qwen-plus")

if not api_key:
    print("❌ 未找到 RATER_2_API_KEY，请在 .env 中填写百炼平台的 API Key")
    exit(1)

print(f"正在检查百炼 API Key: {api_key[:5]}...{api_key[-4:]}")
print(f"请求地址: {base_url}")
print(f"正在唤醒模型: {model_name}")

try:
    # 3. 初始化客户端（指向百炼平台）
    client = OpenAI(
        api_key=api_key,
        base_url=base_url
    )

    # 4. 发送测试对话
    print("正在向百炼平台发送请求...")
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "user", "content": "你好，请回复'百炼连接成功'。"}
        ],
        max_tokens=20
    )

    # 5. 打印结果
    print("\n连接成功！百炼回复：")
    print(response.choices[0].message.content)

except Exception as e:
    print("\n连接失败！错误信息：")
    print(str(e))
