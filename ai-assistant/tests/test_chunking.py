from app.services.chunking import chunk_text, split_sentences


def test_split_sentences_basic():
    text = "First sentence. Second sentence! Third sentence?"
    assert split_sentences(text) == ["First sentence.", "Second sentence!", "Third sentence?"]


def test_split_sentences_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_chunk_text_sliding_window_overlap():
    text = "A. B. C. D. E."
    chunks = chunk_text(text, max_sentences=3, overlap_sentences=1)
    assert chunks == ["A. B. C.", "C. D. E."]


def test_chunk_text_shorter_than_window_returns_single_chunk():
    text = "Only one sentence."
    chunks = chunk_text(text, max_sentences=3, overlap_sentences=1)
    assert chunks == ["Only one sentence."]


def test_chunk_text_rejects_invalid_overlap():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("A. B.", max_sentences=2, overlap_sentences=2)
