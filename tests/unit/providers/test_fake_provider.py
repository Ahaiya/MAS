import pytest

from src.providers.base import LLMRequest, ProviderCallError
from src.providers.fake import FakeProvider, fake_response


def test_fake_provider_replays_responses_in_call_order() -> None:
    provider = FakeProvider([fake_response({"a": 1}), fake_response({"a": 2})])

    first = provider.complete(LLMRequest(prompt="p1"))
    second = provider.complete(LLMRequest(prompt="p2"))

    assert first.structured_data == {"a": 1}
    assert second.structured_data == {"a": 2}


def test_fake_provider_records_requests_in_order() -> None:
    provider = FakeProvider([fake_response({}), fake_response({})])

    provider.complete(LLMRequest(prompt="p1"))
    provider.complete(LLMRequest(prompt="p2"))

    assert [r.prompt for r in provider.requests] == ["p1", "p2"]


def test_fake_provider_raises_when_script_exhausted() -> None:
    provider = FakeProvider([fake_response({"a": 1})])
    provider.complete(LLMRequest(prompt="p1"))

    with pytest.raises(ProviderCallError):
        provider.complete(LLMRequest(prompt="p2"))


def test_fake_response_content_is_json_of_data() -> None:
    response = fake_response({"selected_unit_ids": [1, 2]})
    assert response.structured_data == {"selected_unit_ids": [1, 2]}
    assert "selected_unit_ids" in response.content
