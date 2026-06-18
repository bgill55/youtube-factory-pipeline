"""
Shared LLM query utility for all pipeline agents.
Now uses FreeLLMAPI as the unified router instead of direct provider calls.
"""
import asyncio
import httpx
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from .freellmapi import FreeLLMAPIClient, FreeLLMAPIError, FreeLLMAPIResponse
from .prompts import get_temperature
from youtube_factory.logging_utils import get_logger

log = get_logger("llm_utils")

# Global client instances for reuse
_freellmapi_client: Optional['FreeLLMAPIClient'] = None
_lm_studio_client: Optional['LMStudioClient'] = None


@dataclass
class LMStudioResponse:
    """Response from LM Studio."""
    text: str
    model_used: str
    usage: Dict[str, int]
    finish_reason: str


class LMStudioError(Exception):
    """Exception raised when LM Studio call fails."""
    pass


class LMStudioClient:
    """
    Client for LM Studio local LLM.
    """

    def __init__(
        self,
        base_url: str,
        model_name: str,
        timeout: int = 120,
        default_system_prompt: str = None
    ):
        self.base_url = base_url
        self.model_name = model_name
        self.timeout = timeout
        self.default_system_prompt = default_system_prompt

        self.chat_url = f"{self.base_url}/chat/completions"
        self.client = httpx.Client(timeout=self.timeout)
        log.info(f"LM Studio client initialized for {self.model_name} at {self.base_url}")

    def close(self):
        self.client.close()

    def _build_messages(
        self,
        user_prompt: str,
        system_prompt: str = None,
        message_history: List[Dict[str, str]] = None,
    ) -> List[Dict[str, str]]:
        messages = []
        sys_prompt = system_prompt or self.default_system_prompt
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        if message_history:
            messages.extend(message_history)
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def query(
        self,
        user_prompt: str,
        system_prompt: str = None,
        message_history: List[Dict[str, str]] = None,
        temperature: float = 0.7,
        max_tokens: int = None,
        response_format: str = None,  # "json" for JSON mode
        stream: bool = False
    ) -> LMStudioResponse:
        messages = self._build_messages(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            message_history=message_history,
        )

        body = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens:
            body["max_tokens"] = max_tokens
        
        if response_format == "json":
            # LM Studio (OpenAI-compatible) uses json_schema for structured output
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "strict": True, "schema": {"type": "object"}}}

        headers = {
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
                raise LMStudioError(
                    f"LM Studio error {response.status_code}: {error_text}"
                )

            data = response.json()
            choice = data["choices"][0]
            text = choice["message"].get("content", "") or ""
            finish_reason = choice.get("finish_reason", "stop")
            usage = data.get("usage", {})
            usage_dict = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }

            return LMStudioResponse(
                text=text,
                model_used=self.model_name,
                usage=usage_dict,
                finish_reason=finish_reason,
            )

        except httpx.TimeoutException:
            raise LMStudioError(f"LM Studio request timed out after {self.timeout}s")
        except httpx.RequestError as e:
            raise LMStudioError(f"LM Studio connection error: {str(e)}")
        except json.JSONDecodeError:
            raise LMStudioError("Invalid JSON response from LM Studio")


def _get_lm_studio_client(config: dict) -> Optional[LMStudioClient]:
    global _lm_studio_client
    if _lm_studio_client is None:
        lm_studio_config = config.get("lm_studio", {})
        base_url = lm_studio_config.get("base_url")
        model_name = lm_studio_config.get("model_name")
        if not base_url or not model_name:
            log.debug("LM Studio not configured.")
            return None
        try:
            _lm_studio_client = LMStudioClient(
                base_url=base_url,
                model_name=model_name,
                timeout=int(lm_studio_config.get("timeout", 120))
            )
        except Exception as e:
            log.warning(f"Failed to initialize LM Studio client: {e}")
            return None
    return _lm_studio_client


def _get_freellmapi_client(config: dict) -> Optional[FreeLLMAPIClient]:
    global _freellmapi_client
    
    freellmapi_config = config.get("freellmapi", {})
    base_url = freellmapi_config.get("base_url", "http://localhost:3001/v1")
    api_key = freellmapi_config.get("api_key")
    timeout = int(freellmapi_config.get("timeout", 120))
    
    if not api_key:
        log.warning("FreeLLMAPI key not found. FreeLLMAPI will be unavailable.")
        return None
    
    # Always create a new client with current config to respect timeout changes
    log.info(f"[FreeLLMAPI] Creating client with base_url={base_url}, api_key starts with: {api_key[:20]}..., timeout={timeout}")
    try:
        client = FreeLLMAPIClient(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        return client
    except Exception as e:
        log.warning(f"Failed to initialize FreeLLMAPI client: {e}")
        return None


async def query_llm_async(
    config: dict,
    system_prompt: str,
    user_prompt: str,
    task: str = "default",
    require_json: bool = False,
    message_history: list = None,
    temperature: float = None,
    max_tokens: int = None,
    context: dict = None,
) -> str:
    """
    Async version of query_llm with LM Studio fallback to FreeLLMAPI.
    """
    lm_studio_client = _get_lm_studio_client(config)
    freellmapi_client = _get_freellmapi_client(config)

    if not lm_studio_client and not freellmapi_client:
        raise RuntimeError("No LLM providers configured or available (LM Studio or FreeLLMAPI).")

    # Use task-specific temperature if not explicitly provided
    if temperature is None:
        temperature = get_temperature(task)

    # Prepare context hints for FreeLLMAPI
    freellmapi_context = context or {}
    if require_json:
        freellmapi_context["response_format"] = "json"

    last_error = None

    # 1. Try LM Studio first
    if lm_studio_client:
        log.info("Attempting LLM query with LM Studio...")
        try:
            response = lm_studio_client.query(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                message_history=message_history,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format="json" if require_json else None,
            )
            log.info(f"LM Studio query successful. Model: {response.model_used}")
            return response.text
        except LMStudioError as e:
            log.warning(f"LM Studio query failed: {e}. Falling back to FreeLLMAPI...")
            last_error = e
        except Exception as e:
            log.warning(f"Unexpected error with LM Studio: {e}. Falling back to FreeLLMAPI...")
            last_error = e

    # 2. If LM Studio failed or not available, try FreeLLMAPI
    if freellmapi_client:
        log.info("Attempting LLM query with FreeLLMAPI...")
        max_retries = config.get("freellmapi", {}).get("max_retries", 2)
        for attempt in range(max_retries + 1):
            try:
                response = freellmapi_client.query(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    message_history=message_history,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format="json" if require_json else None,
                    context=freellmapi_context,
                )
                log.info(f"FreeLLMAPI query successful. Routed via: {response.routed_via}, Model: {response.model_used}")
                return response.text
            except FreeLLMAPIError as e:
                last_error = e
                if attempt < max_retries:
                    wait = 2 ** attempt
                    log.warning(f"FreeLLMAPI attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
                    await asyncio.sleep(wait) # Use asyncio.sleep for async context
            except Exception as e:
                log.warning(f"Unexpected error with FreeLLMAPI: {e}. No more retries.")
                last_error = e
                break # No more retries for unexpected errors

    raise RuntimeError(f"All LLM providers failed: {str(last_error)}")


def query_llm(
    config: dict,
    system_prompt: str,
    user_prompt: str,
    task: str = "default",
    require_json: bool = False,
    message_history: list = None,
    temperature: float = None,
    max_tokens: int = None,
    context: dict = None,
) -> str:
    """
    Synchronous wrapper for query_llm_async.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop - safe to use asyncio.run()
        return asyncio.run(query_llm_async(
            config=config,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task=task,
            require_json=require_json,
            message_history=message_history,
            temperature=temperature,
            max_tokens=max_tokens,
            context=context,
        ))
    else:
        # Running inside an event loop - run in a separate thread with its own event loop
        import concurrent.futures
        def run_in_thread():
            return asyncio.run(query_llm_async(
                config=config,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                task=task,
                require_json=require_json,
                message_history=message_history,
                temperature=temperature,
                max_tokens=max_tokens,
                context=context,
            ))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_thread)
            return future.result()


def query_llm_with_history(
    config: dict,
    system_prompt: str,
    conversation_history: list,
    task: str = "default",
    require_json: bool = False,
    temperature: float = None,
    max_tokens: int = None,
) -> str:
    """
    Query with full conversation history.
    """
    return query_llm(
        config=config,
        system_prompt=system_prompt,
        user_prompt=conversation_history[-1]["content"] if conversation_history else "",
        task=task,
        require_json=require_json,
        message_history=conversation_history[:-1] if len(conversation_history) > 1 else None,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# Backward compatibility - old function names
# These now route through the new query_llm logic
def try_gemini(config, system_prompt, user_prompt, require_json=False):
    import warnings
    warnings.warn("try_gemini is deprecated. Use query_llm() which routes through LM Studio or FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt, require_json)


def try_cerebras(config, system_prompt, user_prompt):
    import warnings
    warnings.warn("try_cerebras is deprecated. Use query_llm() which routes through LM Studio or FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)


def try_groq(config, system_prompt, user_prompt):
    import warnings
    warnings.warn("try_groq is deprecated. Use query_llm() which routes through LM Studio or FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)


def try_zai(config, system_prompt, user_prompt):
    import warnings
    warnings.warn("try_zai is deprecated. Use query_llm() which routes through LM Studio or FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)


def try_lm_studio(config, system_prompt, user_prompt):
    import warnings
    warnings.warn("try_lm_studio is deprecated. Use query_llm() which routes through LM Studio or FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)

