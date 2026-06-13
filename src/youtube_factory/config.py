"""
Config loader with .env interpolation for YouTube Factory.
Loads config.json and interpolates ${VAR} or $VAR placeholders with .env values.
"""
import os
import json
import re
from pathlib import Path
from typing import Any, Dict

# Try to load python-dotenv if available
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False


def load_env_file(env_path: str = None) -> Dict[str, str]:
    """Load environment variables from .env file."""
    if env_path is None:
        # Default to project root
        project_root = Path(__file__).parent.parent
        env_path = project_root / ".env"
    
    env_vars = {}
    
    if DOTENV_AVAILABLE:
        load_dotenv(env_path, override=False)
    
    # Also manually parse for variables not loaded by dotenv
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    
    # Also include actual environment variables
    env_vars.update(os.environ)
    
    return env_vars


def interpolate_env(value: Any, env_vars: Dict[str, str]) -> Any:
    """Recursively interpolate ${VAR} or $VAR placeholders in config values."""
    if isinstance(value, str):
        # Replace ${VAR} or $VAR patterns
        def replace_var(match):
            var_name = match.group(1) or match.group(2)
            return env_vars.get(var_name, match.group(0))  # Keep original if not found
        
        # Pattern matches ${VAR} or $VAR (not followed by alphanumeric)
        pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}|\$([A-Z_][A-Z0-9_]*)\b'
        return re.sub(pattern, replace_var, value)
    
    elif isinstance(value, dict):
        return {k: interpolate_env(v, env_vars) for k, v in value.items()}
    
    elif isinstance(value, list):
        return [interpolate_env(v, env_vars) for v in value]
    
    else:
        return value


def load_config(config_path: str = None, env_path: str = None) -> Dict[str, Any]:
    """
    Load config.json and interpolate environment variables from .env file.
    
    Args:
        config_path: Path to config.json (defaults to config/config.json)
        env_path: Path to .env file (defaults to project root .env)
    
    Returns:
        Interpolated config dictionary
    """
    if config_path is None:
        project_root = Path(__file__).parent.parent
        config_path = project_root / "config" / "config.json"
    
    if env_path is None:
        project_root = Path(__file__).parent.parent
        env_path = project_root / ".env"
    
    # Load environment variables
    env_vars = load_env_file(env_path)
    
    # Load config JSON
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    # Interpolate environment variables
    return interpolate_env(config, env_vars)


def get_config_value(config: Dict, key_path: str, default=None):
    """
    Get nested config value using dot notation.
    Example: get_config_value(config, "gemini.api_key")
    """
    keys = key_path.split(".")
    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


if __name__ == "__main__":
    # Test the config loader
    config = load_config()
    print("Loaded config keys:", list(config.keys()))
    print("Gemini API key:", config.get("gemini", {}).get("api_key", "NOT SET")[:20] + "..." if config.get("gemini", {}).get("api_key") else "NOT SET")
    print("Pexels API key:", config.get("pexels_api_key", "NOT SET")[:20] + "..." if config.get("pexels_api_key") else "NOT SET")