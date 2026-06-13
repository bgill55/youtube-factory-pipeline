"""
FreeLLMAPI Client Wrapper for YouTube Factory.
Replaces the multi-provider fallback chain with a single endpoint call.
"""
import os
import json
import asyncio

import httpx
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class FreeLLMAPIResponse:
    """Response from FreeLLMAPI."""
    text: str
    model_used: str
    routed_via: str
    fallback_attempts: int
    usage: Dict[str, int]
    finish_reason: str


class FreeLLMAPIError(Exception):
    """Exception raised when FreeLLMAPI call fails."""
    pass


class FreeLLMAPIClient:
    """
    Client for FreeLLMAPI - the unified LLM router.
    
    Replaces the multi-provider fallback chain in llm_utils.py with a single
    call to FreeLLMAPI's /v1/chat/completions endpoint.
    
    FreeLLMAPI handles:
    - Provider routing (Gemini, Cerebras, Groq, Z.ai, etc.)
    - Penalty/cooldown persistence across restarts
    - System prompt injection at proxy level
    - Failover with exponential backoff
    - Rate limit tracking per provider
    """
    
    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        timeout: int = 120,
        default_system_prompt: str = None
    ):
        """
        Initialize FreeLLMAPI client.
        
        Args:
            base_url: FreeLLMAPI base URL (default: http://localhost:3001/v1)
            api_key: Unified API key from FreeLLMAPI dashboard (freellmapi-xxx...)
            timeout: Request timeout in seconds
            default_system_prompt: Default system prompt (overrides dashboard setting if provided)
        """
        self.base_url = base_url or os.getenv("FREELLAPI_BASE", "http://localhost:3001/v1")
        self.api_key = api_key or os.getenv("FREELLAPI_KEY")
        self.timeout = timeout or int(os.getenv("DEFAULT_TIMEOUT", "120"))
        self.default_system_prompt = default_system_prompt or os.getenv("DEFAULT_SYSTEM_PROMPT", "")
        
        if not self.api_key:
            raise ValueError("FREELLAPI_KEY not configured. Set in .env (FREELLAPI_KEY) or pass api_key parameter. This is the unified freellmapi-xxx key from the FreeLLMAPI dashboard.")
        
        self.chat_url = f"{self.base_url}/chat/completions"
        self.client = httpx.Client(timeout=self.timeout)
    
    def close(self):
        """Close the HTTP client."""
        self.client.close()
    
    def _build_messages(
        self,
        user_prompt: str,
        system_prompt: str = None,
        message_history: List[Dict[str, str]] = None,
        context: Dict[str, Any] = None
    ) -> List[Dict[str, str]]:
        """
        Build message array for FreeLLMAPI.
        
        Args:
            user_prompt: Current user prompt
            system_prompt: System prompt (uses default if not provided)
            message_history: Previous messages for conversation context
            context: Optional context hints (prefer_speed, require_tools, etc.)
        
        Returns:
            List of message dicts for OpenAI-compatible format
        """
        messages = []
        
        # System prompt: priority order - provided > default > dashboard
        sys_prompt = system_prompt or self.default_system_prompt
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        
        # Add conversation history if provided
        if message_history:
            messages.extend(message_history)
        
        # Add current user prompt
        messages.append({"role": "user", "content": user_prompt})
        
        return messages
    
    def query(
        self,
        user_prompt: str,
        system_prompt: str = None,
        message_history: List[Dict[str, str]] = None,
        context: Dict[str, Any] = None,
        temperature: float = 0.7,
        max_tokens: int = None,
        response_format: str = None,  # "json" for JSON mode
        stream: bool = False
    ) -> FreeLLMAPIResponse:
        """
        Query FreeLLMAPI with automatic routing and failover.
        
        Args:
            user_prompt: The user's prompt
            system_prompt: Optional override for system prompt
            message_history: Previous messages for context
            context: Routing hints (prefer_speed, prefer_intelligence, require_tools, etc.)
            temperature: Sampling temperature
            max_tokens: Max completion tokens
            response_format: "json" for JSON mode
            stream: Whether to stream the response
        
        Returns:
            FreeLLMAPIResponse with text, metadata, and routing info
        
        Raises:
            FreeLLMAPIError: If all routing attempts fail
        """
        messages = self._build_messages(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            message_history=message_history,
            context=context
        )
        
        # Build request body
        body = {
            "model": "auto",  # Let FreeLLMAPI router choose
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        
        if max_tokens:
            body["max_tokens"] = max_tokens
        
        if response_format == "json":
            body["response_format"] = {"type": "json_object"}
        
        # Add context hints as extra parameters (FreeLLMAPI ignores unknown fields)
        if context:
            body.update(context)
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        try:
            response = self.client.post(
                self.chat_url,
                headers=headers,
                json=body,
            )
            
            if response.status_code != 200:
                error_text = response.text[:500]
                raise FreeLLMAPIError(
                    f"FreeLLMAPI error {response.status_code}: {error_text}"
                )
            
            data = response.json()
            
            # Extract response
            choice = data["choices"][0]
            message = choice["message"]
            text = message.get("content", "") or ""
            
            # Extract routing metadata
            routed_via = response.headers.get("X-Routed-Via", "unknown")
            fallback_attempts = int(response.headers.get("X-Fallback-Attempts", "0"))
            finish_reason = choice.get("finish_reason", "stop")
            
            # Usage info
            usage = data.get("usage", {})
            usage_dict = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
            
            return FreeLLMAPIResponse(
                text=text,
                model_used=data.get("model", "unknown"),
                routed_via=routed_via,
                fallback_attempts=fallback_attempts,
                usage=usage_dict,
                finish_reason=finish_reason,
            )
            
        except httpx.TimeoutException:
            raise FreeLLMAPIError(f"FreeLLMAPI request timed out after {self.timeout}s")
        except httpx.RequestError as e:
            raise FreeLLMAPIError(f"FreeLLMAPI connection error: {str(e)}")
        except json.JSONDecodeError:
            raise FreeLLMAPIError("Invalid JSON response from FreeLLMAPI")
    
    # Synchronous wrapper for non-async contexts
    def query_sync(self, *args, **kwargs) -> FreeLLMAPIResponse:
        """Synchronous wrapper for query() - runs in new event loop."""
        return self.query(*args, **kwargs)
    
    def get_routing_state(self) -> Dict[str, Any]:
        """
        Get live routing state from FreeLLMAPI dashboard.
        Requires FREELLAPI_DASH_TOKEN environment variable.
        
        Returns:
            Dict with strategy, model scores, penalties, guardrails
        """
        dash_token = os.getenv("FREELLAPI_DASH_TOKEN")
        if not dash_token:
            raise ValueError("FREELLAPI_DASH_TOKEN not configured")
        
        base = self.base_url.replace("/v1", "")
        url = f"{base}/api/fallback/routing"
        headers = {"Authorization": f"Bearer {dash_token}"}
        
        response = self.client.get(url, headers=headers)
        
        if response.status_code != 200:
            # Fallback to public models endpoint
            models_url = f"{self.base_url}/models"
            models_resp = self.client.get(models_url, headers={"Authorization": f"Bearer {self.api_key}"})
            return {"models": models_resp.json().get("data", [])}
        
        return response.json()
    
    def get_routing_state_sync(self) -> Dict[str, Any]:
        """Synchronous wrapper for get_routing_state()."""
        return self.get_routing_state()


# Convenience function for simple queries
def query_freellmapi(
    user_prompt: str,
    system_prompt: str = None,
    **kwargs
) -> FreeLLMAPIResponse:
    """
    Convenience function for one-off queries.
    
    Usage:
        response = await query_freellmapi("What is 2+2?")
        print(response.text)
        print(f"Routed via: {response.routed_via}")
    """
    client = FreeLLMAPIClient()
    try:
        return client.query(user_prompt, system_prompt, **kwargs)
    finally:
        client.close()


def query_freellmapi_sync(user_prompt: str, system_prompt: str = None, **kwargs) -> FreeLLMAPIResponse:
    """Synchronous convenience wrapper — calls query_freellmapi() directly (it is already synchronous)."""
    return query_freellmapi(user_prompt, system_prompt, **kwargs)