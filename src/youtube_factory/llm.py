"""
Shared LLM query utility for all pipeline agents.
Now uses FreeLLMAPI as the unified router instead of direct provider calls.
"""
import asyncio
from typing import Optional
from youtube_factory.freellmapi import FreeLLMAPIClient, FreeLLMAPIError, FreeLLMAPIResponse
from youtube_factory.prompts import get_temperature


# Global client instance for reuse
_freellmapi_client: Optional['FreeLLMAPIClient'] = None


def _get_client(config: dict) -> FreeLLMAPIClient:
    """Get or create the FreeLLMAPI client singleton."""
    global _freellmapi_client
    
    if _freellmapi_client is None:
        # Check for freellmapi section first, then fall back to gemini
        freellmapi_config = config.get("freellmapi", {})
        base_url = freellmapi_config.get("base_url", "http://localhost:3001/v1")
        api_key = freellmapi_config.get("api_key") or config.get("gemini", {}).get("api_key")
        timeout = int(freellmapi_config.get("timeout", 120))
        
        if not api_key:
            raise ValueError("No FreeLLMAPI key found. Set freellmapi.api_key in config (loaded from FREELLAPI_KEY env var).")
        
        _freellmapi_client = FreeLLMAPIClient(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
    
    return _freellmapi_client


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
    Async version of query_llm using FreeLLMAPI.
    
    Args:
        config: Pipeline config dict
        system_prompt: System prompt for the model
        user_prompt: User's prompt
        task: Task type for temperature selection (idea, script, visual_prompt, etc.)
        require_json: Whether to request JSON response format
        message_history: Previous conversation messages
        temperature: Sampling temperature (auto-set per task if not provided)
        max_tokens: Max completion tokens
        context: Optional routing hints (prefer_speed, require_tools, etc.)
    
    Returns:
        Model response text
    
    Raises:
        RuntimeError: If FreeLLMAPI call fails
    """
    client = _get_client(config)
    
    # Prepare context hints
    context = context or {}
    if require_json:
        context["response_format"] = "json"
    
    # Use task-specific temperature if not explicitly provided
    if temperature is None:
        temperature = get_temperature(task)
    
    import time
    max_retries = config.get("freellmapi", {}).get("max_retries", 2)
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            response = client.query(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                message_history=message_history,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format="json" if require_json else None,
                context=context,
            )
            return response.text
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"[LLM] Attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
    
    raise RuntimeError(f"FreeLLMAPI call failed after {max_retries + 1} attempts: {str(last_error)}")


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
    Synchronous wrapper for query_llm_async using FreeLLMAPI.

    This maintains backward compatibility with the old llm_utils.py interface
    while routing all calls through FreeLLMAPI.

    Args:
        config: Pipeline config dict
        system_prompt: System prompt for the model
        user_prompt: User's prompt
        task: Task type for temperature selection (idea, script, visual_prompt, etc.)
        require_json: Whether to request JSON response format
        message_history: Previous conversation messages (optional)
        temperature: Sampling temperature (auto-set per task if not provided)
        max_tokens: Max completion tokens
        context: Optional routing hints (prefer_speed, require_tools, etc.)

    Returns:
        Model response text

    Raises:
        RuntimeError: If FreeLLMAPI call fails
    """
    # Handle async properly in both sync and async contexts (e.g., Flask event loop)
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
        # to avoid deadlock with run_coroutine_threadsafe + future.result() on same thread
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
    
    Args:
        config: Pipeline config dict
        system_prompt: System prompt
        conversation_history: List of {"role": "user/assistant", "content": "..."}
        task: Task type for temperature selection
        require_json: Whether to request JSON response
        temperature: Sampling temperature (auto-set per task if not provided)
        max_tokens: Max completion tokens
    
    Returns:
        Model response text
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
def try_gemini(config, system_prompt, user_prompt, require_json=False):
    """Deprecated - kept for backward compatibility. Use query_llm instead."""
    import warnings
    warnings.warn("try_gemini is deprecated. Use query_llm() which routes through FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt, require_json)


def try_cerebras(config, system_prompt, user_prompt):
    """Deprecated - kept for backward compatibility."""
    import warnings
    warnings.warn("try_cerebras is deprecated. Use query_llm() which routes through FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)


def try_groq(config, system_prompt, user_prompt):
    """Deprecated - kept for backward compatibility."""
    import warnings
    warnings.warn("try_groq is deprecated. Use query_llm() which routes through FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)


def try_zai(config, system_prompt, user_prompt):
    """Deprecated - kept for backward compatibility."""
    import warnings
    warnings.warn("try_zai is deprecated. Use query_llm() which routes through FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)


def try_lm_studio(config, system_prompt, user_prompt):
    """Deprecated - kept for backward compatibility."""
    import warnings
    warnings.warn("try_lm_studio is deprecated. Use query_llm() which routes through FreeLLMAPI.", DeprecationWarning)
    return query_llm(config, system_prompt, user_prompt)