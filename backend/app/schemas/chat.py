"""Provider/model catalog served by GET /api/v1/chat/models (todo 30 extraction)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.privacy import EXTERNAL_PROVIDERS, LOCAL_PROVIDERS


class ModelOption(BaseModel):
    id: str
    name: str
    description: str | None = None
    recommended: bool = False


class ProviderOption(BaseModel):
    id: str
    name: str
    type: str
    is_external: bool
    description: str
    default_model: str
    models: list[ModelOption]


def _provider(
    id: str,
    name: str,
    description: str,
    default_model: str,
    models: list[ModelOption],
) -> ProviderOption:
    return ProviderOption(
        id=id,
        name=name,
        type="local" if id in LOCAL_PROVIDERS else "cloud",
        is_external=id in EXTERNAL_PROVIDERS,
        description=description,
        default_model=default_model,
        models=models,
    )


PROVIDERS: list[ProviderOption] = [
    _provider(
        id="ollama",
        name="Ollama (Local)",
        description="Local on-device inference with zero data transmission. Strict privacy compliant.",
        default_model="llama3.2",
        models=[
            ModelOption(
                id="llama3.2",
                name="Llama 3.2 (3B)",
                description="Lightweight, fast, local privacy",
                recommended=True,
            ),
            ModelOption(
                id="llama3.1",
                name="Llama 3.1 (8B)",
                description="Balanced general reasoning",
            ),
            ModelOption(
                id="qwen2.5",
                name="Qwen 2.5 (7B/14B)",
                description="Excellent Arabic & multilingual legal understanding",
                recommended=True,
            ),
            ModelOption(
                id="mistral",
                name="Mistral (7B)",
                description="Concise legal summarization",
            ),
            ModelOption(
                id="deepseek-r1",
                name="DeepSeek R1 Distill",
                description="Deep step-by-step reasoning",
            ),
        ],
    ),
    _provider(
        id="openrouter",
        name="OpenRouter (Unified Cloud)",
        description="Access dozens of state-of-the-art models via OpenRouter unified gateway.",
        default_model="anthropic/claude-3.5-sonnet",
        models=[
            ModelOption(
                id="anthropic/claude-3.5-sonnet",
                name="Claude 3.5 Sonnet",
                description="Top benchmark for legal reasoning and drafting",
                recommended=True,
            ),
            ModelOption(
                id="openai/gpt-4o",
                name="GPT-4o",
                description="High-capability omnimodel",
            ),
            ModelOption(
                id="google/gemini-2.0-flash",
                name="Gemini 2.0 Flash",
                description="Ultra fast with large context window",
                recommended=True,
            ),
            ModelOption(
                id="meta-llama/llama-3.3-70b-instruct",
                name="Llama 3.3 70B Instruct",
                description="High performance open-weights",
            ),
            ModelOption(
                id="deepseek/deepseek-r1",
                name="DeepSeek R1",
                description="Frontier reasoning for complex statutory disputes",
            ),
        ],
    ),
    _provider(
        id="openai",
        name="OpenAI",
        description="Direct OpenAI API connection (requires OPENAI_API_KEY).",
        default_model="gpt-4o",
        models=[
            ModelOption(
                id="gpt-4o",
                name="GPT-4o",
                description="Flagship intelligent model",
                recommended=True,
            ),
            ModelOption(
                id="gpt-4o-mini",
                name="GPT-4o Mini",
                description="Fast, cost-efficient analysis",
            ),
            ModelOption(
                id="o1-mini",
                name="o1-mini",
                description="Advanced reasoning for complex statutory analysis",
            ),
        ],
    ),
    _provider(
        id="anthropic",
        name="Anthropic",
        description="Direct Anthropic Claude API connection (requires ANTHROPIC_API_KEY).",
        default_model="claude-3-5-sonnet-latest",
        models=[
            ModelOption(
                id="claude-3-5-sonnet-latest",
                name="Claude 3.5 Sonnet",
                description="State-of-the-art legal precision",
                recommended=True,
            ),
            ModelOption(
                id="claude-3-5-haiku-latest",
                name="Claude 3.5 Haiku",
                description="Fast and concise responses",
            ),
        ],
    ),
    _provider(
        id="google",
        name="Google Gemini",
        description="Direct Google Gemini API connection (requires GEMINI_API_KEY).",
        default_model="gemini-2.0-flash",
        models=[
            ModelOption(
                id="gemini-2.0-flash",
                name="Gemini 2.0 Flash",
                description="High-speed next-gen model",
                recommended=True,
            ),
            ModelOption(
                id="gemini-1.5-pro",
                name="Gemini 1.5 Pro",
                description="Deep document analysis & massive context",
            ),
        ],
    ),
    _provider(
        id="groq",
        name="Groq",
        description="Ultra-low latency LPU cloud inference (requires GROQ_API_KEY).",
        default_model="llama-3.3-70b-versatile",
        models=[
            ModelOption(
                id="llama-3.3-70b-versatile",
                name="Llama 3.3 70B (Groq)",
                description="Near-instant token generation",
                recommended=True,
            ),
            ModelOption(
                id="mixtral-8x7b-32768",
                name="Mixtral 8x7B (Groq)",
                description="Fast mixture of experts",
            ),
        ],
    ),
]

assert {p.id for p in PROVIDERS} == EXTERNAL_PROVIDERS | LOCAL_PROVIDERS


class ChatIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    conversation_id: int | None = None
    content: str
    consent: bool = False
    system: str | None = None


class RagChatIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int | None = None
    content: str = Field(min_length=1)
    conversation_id: int | None = None
    consent: bool = False
    provider: str | None = None
    model: str | None = None


class ModelsResponse(BaseModel):
    current_provider: str
    current_model: str
    privacy_mode: str
    providers: list[ProviderOption]
