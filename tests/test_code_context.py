from code_context import build_context_message, read_clipboard


# ---------- build_context_message ----------

def test_build_context_message_none_for_none():
    assert build_context_message(None) is None


def test_build_context_message_none_for_empty():
    assert build_context_message("") is None


def test_build_context_message_none_for_whitespace():
    assert build_context_message("   \n\t  ") is None


def test_build_context_message_wraps_code():
    msg = build_context_message("def foo():\n    return 1")
    assert "def foo():" in msg
    assert msg.startswith("[Systemhinweis:")
    assert msg.endswith("]")


def test_build_context_message_truncates_long_text():
    long_text = "x" * 5000
    msg = build_context_message(long_text, max_chars=100)
    assert "gekürzt" in msg
    assert msg.count("x") == 100


def test_build_context_message_no_truncation_note_when_short():
    msg = build_context_message("short code", max_chars=100)
    assert "gekürzt" not in msg


# ---------- read_clipboard ----------

def test_read_clipboard_returns_text():
    assert read_clipboard(paste_fn=lambda: "print('hi')") == "print('hi')"


def test_read_clipboard_returns_none_for_empty():
    assert read_clipboard(paste_fn=lambda: "") is None


def test_read_clipboard_returns_none_for_whitespace():
    assert read_clipboard(paste_fn=lambda: "   ") is None


def test_read_clipboard_returns_none_on_error():
    def boom():
        raise RuntimeError("no clipboard access")

    assert read_clipboard(paste_fn=boom) is None
