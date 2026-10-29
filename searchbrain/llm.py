"""Small helper for calls to the NVIDIA model (OpenAI-compatible API)."""

from openai import OpenAI

from searchbrain import config

_client: OpenAI | None = None


def client() -> OpenAI:
    """Return one shared client, created on first use."""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=config.NVIDIA_BASE_URL,
            api_key=config.get("NVIDIA_API_KEY"),
            timeout=90,
            max_retries=0,  # callers decide whether to retry
        )
    return _client


def chat(system: str, user: str, max_tokens: int = 4096) -> str:
    """Send one system + user message and return the answer text.

    The answer is streamed: the timeout then applies to each piece of the answer,
    not to the whole answer, so long answers do not time out.
    """
    stream = client().chat.completions.create(
        model=config.NVIDIA_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        max_tokens=max_tokens,
        stream=True,
    )
    parts = [chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices]
    return "".join(parts).strip()
