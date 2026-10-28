import json

import httpx
import pytest

from course_notes_rag.generation.llm import (
    FakeLLM, LLMError, LLMUnavailableError, OllamaClient, OpenAICompatClient,
)

MSG = [{"role": "user", "content": "hi"}]


def ollama(handler):
    return OllamaClient("http://ollama.test", "qwen2.5:7b", transport=httpx.MockTransport(handler))


def openai(handler):
    return OpenAICompatClient("http://llm.test/v1", "qwen-plus", "key",
                              transport=httpx.MockTransport(handler))


# --- Ollama -----------------------------------------------------------------------


def test_ollama_chat_and_payload():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "hello"}})

    client = ollama(handler)
    assert client.chat(MSG, temperature=0.0, json_schema={"type": "object"}) == "hello"
    assert seen["model"] == "qwen2.5:7b" and seen["stream"] is False
    assert seen["format"] == {"type": "object"}
    assert seen["options"]["temperature"] == 0.0 and seen["options"]["num_ctx"] == 8192


def test_ollama_stream_yields_deltas_until_done():
    lines = [
        {"message": {"content": "Hel"}, "done": False},
        {"message": {"content": "lo"}, "done": False},
        {"message": {"content": ""}, "done": True},
        {"message": {"content": "ignored"}, "done": False},
    ]
    body = "\n".join(json.dumps(x) for x in lines).encode()
    client = ollama(lambda r: httpx.Response(200, content=body))
    assert list(client.stream_chat(MSG)) == ["Hel", "lo"]


def test_ollama_stream_error_chunk():
    body = json.dumps({"error": "out of memory"}).encode()
    with pytest.raises(LLMError, match="out of memory"):
        list(ollama(lambda r: httpx.Response(200, content=body)).stream_chat(MSG))


def test_ollama_missing_model_is_unavailable():
    client = ollama(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(LLMUnavailableError, match="ollama pull qwen2.5:7b"):
        client.chat(MSG)
    with pytest.raises(LLMUnavailableError):
        list(client.stream_chat(MSG))


def test_ollama_server_error_and_malformed():
    with pytest.raises(LLMError, match="500"):
        ollama(lambda r: httpx.Response(500, json={"error": "boom"})).chat(MSG)
    with pytest.raises(LLMError, match="Malformed"):
        ollama(lambda r: httpx.Response(200, json={"nope": 1})).chat(MSG)
    with pytest.raises(LLMError, match="Malformed"):
        list(ollama(lambda r: httpx.Response(200, content=b"not json")).stream_chat(MSG))


def test_ollama_connection_errors():
    def refuse(request):
        raise httpx.ConnectError("refused")

    client = ollama(refuse)
    with pytest.raises(LLMUnavailableError, match="Is Ollama running"):
        client.chat(MSG)
    with pytest.raises(LLMUnavailableError):
        list(client.stream_chat(MSG))

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMError, match="too long"):
        ollama(slow).chat(MSG)


def test_ollama_rejects_empty_messages():
    with pytest.raises(ValueError):
        ollama(lambda r: httpx.Response(200)).chat([])


def test_ollama_health():
    ok = ollama(lambda r: httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})).health()
    assert ok["ok"] is True
    missing = ollama(lambda r: httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})).health()
    assert missing["ok"] is False and "ollama pull" in missing["detail"]

    def refuse(request):
        raise httpx.ConnectError("refused")

    assert ollama(refuse).health()["ok"] is False


# --- OpenAI-compatible ----------------------------------------------------------------


def test_openai_chat_with_schema():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    assert openai(handler).chat(MSG, json_schema={"title": "Quiz", "type": "object"}) == "{}"
    assert seen["auth"] == "Bearer key"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "Quiz"


def test_openai_stream():
    events = [
        {"choices": [{"delta": {"content": "A"}}]},
        {"choices": [{"delta": {"content": "B"}}]},
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    assert list(openai(lambda r: httpx.Response(200, content=body.encode())).stream_chat(MSG)) == ["A", "B"]


def test_openai_errors():
    with pytest.raises(LLMError, match="401"):
        openai(lambda r: httpx.Response(401, text="bad key")).chat(MSG)
    with pytest.raises(LLMError, match="Malformed"):
        openai(lambda r: httpx.Response(200, json={"choices": []})).chat(MSG)
    with pytest.raises(LLMError):
        list(openai(lambda r: httpx.Response(500, text="x")).stream_chat(MSG))

    def refuse(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailableError):
        openai(refuse).chat(MSG)
    assert openai(refuse).health()["ok"] is False
    with pytest.raises(ValueError):
        openai(refuse).chat([])


def test_openai_health_ok():
    assert openai(lambda r: httpx.Response(200, json={"data": []})).health()["ok"] is True


# --- Fake ---------------------------------------------------------------------------


def test_fake_llm_scripted_and_exhausted():
    fake = FakeLLM(["one", LLMError("boom"), "three"])
    assert fake.chat(MSG) == "one"
    with pytest.raises(LLMError):
        fake.chat(MSG)
    assert "".join(fake.stream_chat(MSG)) == "three"
    with pytest.raises(LLMError, match="no scripted"):
        fake.chat(MSG)
    assert len(fake.calls) == 4


def test_fake_llm_handler():
    fake = FakeLLM(handler=lambda m, s: "schema" if s else "text")
    assert fake.chat(MSG, json_schema={"type": "object"}) == "schema" and fake.chat(MSG) == "text"
