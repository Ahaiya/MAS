import os
from dotenv import load_dotenv
from openai import OpenAI  # DeepSeek 完美兼容 openai 库，直接用就行

# 1. 强制加载当前目录下的 .env 文件
load_dotenv()

# 2. 获取 Key 和 Base URL
api_key = os.getenv("LLM_API_KEY") 
# 如果 .env 没写 BASE_URL，默认用 DeepSeek 的官方地址
base_url = os.getenv("LLM_API_BASE", "https://api.deepseek.com") 
# 如果 .env 没写模型名，默认用 deepseek-chat (即 DeepSeek-V3)
model_name = os.getenv("LLM_MODEL", "deepseek-chat")

print(f"🔍 正在检查 DeepSeek API Key: {api_key[:5]}...{api_key[-4:] if api_key else '未找到'}")
print(f"🌐 请求地址: {base_url}")
print(f"🤖 正在唤醒模型: {model_name}")

try:
    # 3. 初始化客户端 (把 base_url 指向 DeepSeek)
    client = OpenAI(
        api_key=api_key,
        base_url=base_url
    )

    # 4. 发送测试对话
    print("⏳ 正在向 DeepSeek 发送请求...")
    response = client.chat.completions.create(
        model="model_name",
        messages=[
            {"role": "user", "content": "你好，请回复'DeepSeek 连接成功'。"}
        ],
        max_tokens=20
    )
    
    # 5. 打印结果
    print("\n✅ 连接成功！DeepSeek 回复：")
    print(response.choices[0].message.content)

except Exception as e:
    print("\n❌ 连接失败！错误信息：")
    print(str(e))