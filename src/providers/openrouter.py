from __future__ import annotations


class OpenRouterAdapter:
    name = "openrouter"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def discover_models(self) -> list[dict]:
        # Stubbed default discovery for MVP bootstrap.
        return [
            {
                "provider": self.name,
                "model_name": "openrouter/auto",
                "is_healthy": 1,
                "score": 100.0,
                "supports_tools": 1,
                "supports_vision": 1,
                "supports_streaming": 1,
            }
        ]

    async def chat_completion(self, payload: dict, model_name: str) -> dict:
        messages = payload.get("messages", [])
        prompt = messages[-1].get("content", "") if messages else ""
        return {
            "id": "chatcmpl-freelunch-stub",
            "object": "chat.completion",
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"Echo: {prompt}"},
                    "finish_reason": "stop",
                }
            ],
        }
