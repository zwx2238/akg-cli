import os
import json
from typing import List, Dict, Optional

import httpx


class DeepSeekAPI:
    """DeepSeek API 客户端"""

    BASE_URL = "https://api.deepseek.com/v1/chat/completions"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY 环境变量或通过参数传入 API Key")

        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    def chat(self, messages: List[Dict[str, str]], model: str = "deepseek-chat") -> str:
        """发送聊天请求"""
        response = self.client.post(
            self.BASE_URL,
            json={
                "model": model,
                "messages": messages,
                "stream": False,
            },
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]

    def chat_stream(self, messages: List[Dict[str, str]], model: str = "deepseek-chat"):
        """流式聊天请求"""
        with self.client.stream(
            "POST",
            self.BASE_URL,
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue
