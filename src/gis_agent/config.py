from dotenv import load_dotenv, find_dotenv
import os
import ee
from agents import (
    AsyncOpenAI,
    ModelRetrySettings,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RetryDecision,
    RetryPolicyContext,
    RunConfig,
)

# Load environment variables
load_dotenv(find_dotenv())

def initialize_earth_engine():
    """Initialize Earth Engine"""
    try:
        ee.Initialize(project='ee-ewe111vijay')
        print("Earth Engine initialized successfully")
    except Exception as e:
        print(f"Error initializing Earth Engine: {e}")

def _transient_server_error_retry(context: RetryPolicyContext) -> RetryDecision:
    status = context.normalized.status_code
    return RetryDecision(
        retry=(
            context.normalized.is_network_error
            or context.normalized.is_timeout
            or (status is not None and status >= 500)
        )
    )


def _model_run_config(api_key: str, base_url: str, model_name: str, *, groq: bool = False):
    provider = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    model = OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=provider,
    )
    return RunConfig(
        model=model,
        model_provider=provider,
        model_settings=ModelSettings(
            retry=ModelRetrySettings(
                max_retries=2 if groq else 3,
                backoff={
                    "initial_delay": 1,
                    "max_delay": 8,
                    "multiplier": 2,
                    "jitter": True,
                },
                policy=_transient_server_error_retry if groq else None,
            )
        ),
        tracing_disabled=True,
    )


def setup_groq():
    """Create the primary Groq model config, or None when no API key is set."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return _model_run_config(
        api_key,
        "https://api.groq.com/openai/v1",
        "qwen/qwen3.8-27b",
        groq=True,
    )


def setup_gemini():
    """Create the Gemini fallback model config, or None when no API key is set."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    return _model_run_config(
        api_key,
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-3.8-flash",
    )