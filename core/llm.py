"""LLM 客户端：OpenAI 兼容协议实现（当前接入火山引擎方舟 GLM-5.3-Flash）。

只依赖 `/chat/completions` 这一通用接口，换厂商只需改 .env 的
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 三行，业务代码零改动。

API_KEY 为空时 chat() 返回 None，调用方自行降级处理。
"""
import requests

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MODEL_FALLBACK


class LLMClient:
    def __init__(self):
        self.base_url = (LLM_BASE_URL or "").rstrip("/")
        self.api_key = LLM_API_KEY or ""
        self.last_error: str | None = None
        # 去重且保序：主模型优先，其次兜底模型
        models: list[str] = []
        for m in (LLM_MODEL, LLM_MODEL_FALLBACK):
            if m and m not in models:
                models.append(m)
        self.models = models

    @property
    def available(self) -> bool:
        return bool(self.api_key) and "在这里" not in self.api_key and bool(self.base_url)

    @property
    def model(self) -> str:
        """当前生效的模型名。"""
        return self.models[0] if self.models else ""

    def chat(self, prompt: str, system: str | None = None, temperature: float = 0.3) -> str | None:
        """单轮对话，返回文本；未配置 Key 或全部模型调用失败返回 None。"""
        if not self.available:
            return None

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for idx, model in enumerate(self.models):
            payload = {"model": model, "messages": messages, "temperature": temperature}
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=payload, timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if idx > 0:
                        print(f"[LLMClient] 已使用备用模型 {model} 完成调用")
                    return data["choices"][0]["message"]["content"]
                # 非 200：记录错误体，尝试下一个模型
                self.last_error = f"{model}: HTTP {resp.status_code} {resp.text[:200]}"
                print(f"[LLMClient] 模型 {model} 调用失败: {self.last_error}")
            except Exception as e:
                self.last_error = f"{model}: {e}"
                print(f"[LLMClient] 模型 {model} 请求异常: {e}")

            if idx < len(self.models) - 1:
                print(f"[LLMClient] 降级到备用模型 {self.models[idx + 1]}")
        return None


# 全局单例（其他模块统一 from core.llm import llm）
llm = LLMClient()
