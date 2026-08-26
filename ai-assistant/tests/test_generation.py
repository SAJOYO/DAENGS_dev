from unittest.mock import MagicMock, patch

from app.services.generation import _NON_KOREAN_FALLBACK, generate_answer


def _fake_ollama_client(answers: list[str]):
    """chat()을 호출할 때마다 answers 리스트에서 순서대로 하나씩 반환하는 가짜 클라이언트."""
    mock_client = MagicMock()
    mock_client.chat.side_effect = [
        {"message": {"content": a}} for a in answers
    ]
    return mock_client


@patch("app.services.generation.ollama.Client")
def test_generate_answer_returns_korean_answer_directly(mock_client_cls):
    mock_client_cls.return_value = _fake_ollama_client(["포도는 강아지에게 위험합니다."])

    result = generate_answer("포도 먹여도 돼?", ["포도는 위험합니다."])

    assert result == "포도는 강아지에게 위험합니다."
    mock_client_cls.return_value.chat.assert_called_once()


@patch("app.services.generation.ollama.Client")
def test_generate_answer_retries_once_when_non_korean(mock_client_cls):
    mock_client_cls.return_value = _fake_ollama_client(
        ["Grapes are dangerous for dogs.", "포도는 강아지에게 위험합니다."]
    )

    result = generate_answer("포도 먹여도 돼?", ["포도는 위험합니다."])

    assert result == "포도는 강아지에게 위험합니다."
    assert mock_client_cls.return_value.chat.call_count == 2


@patch("app.services.generation.ollama.Client")
def test_generate_answer_falls_back_when_retry_also_non_korean(mock_client_cls):
    mock_client_cls.return_value = _fake_ollama_client(
        ["Grapes are dangerous for dogs.", "Still in English."]
    )

    result = generate_answer("포도 먹여도 돼?", ["포도는 위험합니다."])

    assert result == _NON_KOREAN_FALLBACK
    assert mock_client_cls.return_value.chat.call_count == 2
