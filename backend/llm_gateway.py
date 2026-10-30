import json
import re
import base64
import logging
import httpx
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from config import settings

logger = logging.getLogger("llm_gateway")
logger.setLevel(logging.INFO)

def mask_key(key: str) -> str:
    """Masks secret API keys to ensure they are never written to logs."""
    if not key:
        return "<not_configured>"
    if len(key) <= 8:
        return "***"
    return f"{key[:6]}...{key[-4:]}"

class LLMGateway:
    """
    Provider-independent LLM Gateway supporting OpenAI-compatible APIs
    (NVIDIA NIM, OpenAI, Ollama, LM Studio, vLLM, etc.)
    with text, tool-calling, and multimodal vision capabilities.
    """
    def __init__(self):
        pass

    @property
    def base_url(self) -> str:
        return settings.LLM_BASE_URL.rstrip("/")

    @property
    def api_key(self) -> str:
        return settings.LLM_API_KEY

    @property
    def chat_model(self) -> str:
        return settings.LLM_MODEL

    @property
    def vision_model(self) -> str:
        return settings.LLM_VISION_MODEL

    def get_status(self) -> Dict[str, Any]:
        return {
            "provider": settings.LLM_PROVIDER,
            "has_api_key": bool(self.api_key),
            "masked_key": mask_key(self.api_key),
            "base_url": self.base_url,
            "chat_model": self.chat_model,
            "vision_model": self.vision_model,
            "timeout": settings.LLM_TIMEOUT
        }

    async def chat_complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 2048,
        response_format: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Executes a standard chat completion request across any configured provider.
        """
        selected_model = model or self.chat_model
        if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            return {
                "success": False,
                "error": "No API key configured for remote LLM provider",
                "content": None
            }

        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: Dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format

        url = f"{self.base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    err_msg = f"LLM API returned status {resp.status_code}: {resp.text[:300]}"
                    logger.warning(err_msg)
                    return {
                        "success": False,
                        "error": err_msg,
                        "content": f"I encountered an error connecting to the LLM service ({resp.status_code}). Please verify your API key and endpoint in Settings.",
                        "tool_calls": []
                    }
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "") or ""
                if not content and message.get("reasoning_content"):
                    r_text = message.get("reasoning_content", "").strip()
                    # Check if reasoning contains an explicit final response statement
                    match = re.search(r'(?:Response|Conclusion|Final answer):\s*["\']?([^"\'\r\n]+)["\']?', r_text, re.IGNORECASE)
                    if match:
                        content = match.group(1).strip()
                    else:
                        content = ""
                return {
                    "success": bool(content),
                    "content": content,
                    "tool_calls": message.get("tool_calls", []),
                    "raw": data
                }
        except httpx.TimeoutException:
            logger.error("LLM Gateway request timed out")
            return {
                "success": False,
                "error": "Request timed out",
                "content": "The LLM request timed out. Please check your network connection or model endpoint.",
                "tool_calls": []
            }
        except Exception as e:
            logger.error(f"LLM Gateway exception: {e}")
            return {
                "success": False,
                "error": str(e),
                "content": f"Error communicating with LLM gateway: {str(e)}",
                "tool_calls": []
            }

    async def vision_analyze(
        self,
        prompt: str,
        image_b64: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        detail: str = "high"
    ) -> Dict[str, Any]:
        """
        Multimodal visual analysis of a desktop/window screenshot.
        Sends prompt + JPEG base64 data URL to the vision-capable LLM.
        """
        selected_model = model or self.vision_model
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append({
                "role": "system",
                "content": (
                    "You are the Vision Agent for a Windows OS Assistant. "
                    "Analyze screenshots with pixel-level precision, identify visible apps, "
                    "buttons, dialogs, error messages, and UI interaction coordinates."
                )
            })

        # Standard OpenAI/NIM image content payload
        image_url = f"data:image/jpeg;base64,{image_b64}" if not image_b64.startswith("data:") else image_b64
        user_content: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": image_url,
                    "detail": detail
                }
            }
        ]
        messages.append({"role": "user", "content": user_content})

        payload = {
            "model": selected_model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1500
        }
        url = f"{self.base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    err_msg = f"Vision API returned {resp.status_code}: {resp.text[:300]}"
                    logger.warning(err_msg)
                    return {
                        "success": False,
                        "error": err_msg,
                        "analysis": f"Vision analysis unavailable ({resp.status_code})."
                    }
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                return {
                    "success": True,
                    "analysis": message.get("content", ""),
                    "raw": data
                }
        except Exception as e:
            logger.error(f"Vision analyze exception: {e}")
            return {
                "success": False,
                "error": str(e),
                "analysis": f"Vision analysis error: {str(e)}"
            }

llm_gateway = LLMGateway()
