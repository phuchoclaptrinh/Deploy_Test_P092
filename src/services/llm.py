"""Chat model factory for the Agent v3 pipeline.

OpenAI is the primary provider. When ANTHROPIC_API_KEY and
ANTHROPIC_FALLBACK_MODEL are both set, an Anthropic model is composed in behind
it, so an OpenAI call that errors retries on Anthropic instead of failing the
whole analysis run.

`with_fallbacks()` is applied here rather than at the call site because
`RunnableWithFallbacks.with_structured_output()` pushes the schema binding into
the primary and every fallback, so the agent nodes keep binding their Pydantic
schemas exactly as before and get the retry for free.
"""

from __future__ import annotations

import logging
import re

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from src.config import get_settings

logger = logging.getLogger(__name__)


def openai_no_thinking_kwargs(model_name: str, *, reasoning_effort: str = "none") -> dict[str, str]:
    """Return the OpenAI parameter that disables reasoning where supported.

    Older chat models do not expose reasoning at all, so sending an unsupported
    parameter would turn a latency optimisation into a request failure.  The
    bare GPT-5 generation does not support ``none`` either; GPT-5.1 and newer
    do.  Keep this compatibility decision in one place because analysis and
    the AI-assignment rollback both use ChatOpenAI.
    """
    name = model_name.strip().lower()
    if not name.startswith("gpt-5"):
        return {}
    match = re.match(r"^gpt-5\.(\d+)(?:[-.]|$)", name)
    if match and int(match.group(1)) >= 1:
        return {"reasoning_effort": reasoning_effort}
    logger.info("OpenAI model %s does not support reasoning_effort=none; omitting it.", model_name)
    return {}


def get_llm() -> Runnable:
    settings = get_settings()
    primary = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.openai_api_key,
        temperature=settings.llm_temperature,
        **openai_no_thinking_kwargs(settings.model_name, reasoning_effort=settings.llm_reasoning_effort),
    )

    if not (settings.anthropic_api_key and settings.anthropic_fallback_model):
        return primary

    fallback = ChatAnthropic(
        model=settings.anthropic_fallback_model,
        api_key=settings.anthropic_api_key,
        temperature=settings.llm_temperature,
    )
    logger.info("Anthropic fallback enabled (%s).", settings.anthropic_fallback_model)
    return primary.with_fallbacks([fallback])
