import re
import sys
import pytest
import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent))
import main


# --- translit ---

def test_translit_basic():
    assert main.translit("\u041f\u0440\u0438\u0432\u0435\u0442") == "Privet"

def test_translit_mixed():
    assert main.translit("hello \u043c\u0438\u0440") == "hello mir"

def test_translit_no_cyrillic():
    assert main.translit("hello 123") == "hello 123"


# --- strip_html ---

def test_strip_html_basic():
    assert main.strip_html("<p>Hello world</p>") == "Hello world"

def test_strip_html_nested():
    result = main.strip_html("<p><b>A</b> B</p>"); assert "A" in result and "B" in result

def test_strip_html_empty():
    assert main.strip_html("") == ""

def test_strip_html_entities():
    # HTML entities are decoded by HTMLParser
    result = main.strip_html("<p>\u0422\u0435\u043a\u0441\u0442</p>")
    assert "\u0422\u0435\u043a\u0441\u0442" in result


# --- _blen ---

def test_blen_latin():
    assert main._blen("abc") == 3

def test_blen_cyrillic():
    # each Cyrillic char = 2 bytes in UTF-8
    assert main._blen("\u0430\u0431\u0432") == 6


# --- split_text ---

def test_split_text_no_split_needed():
    assert main.split_text("hello", 20) == ["hello"]

def test_split_text_exact_limit():
    assert main.split_text("ab", 2) == ["ab"]

def test_split_text_splits_on_space():
    parts = main.split_text("hello world", 8)
    assert parts == ["hello", "world"]
    assert all(main._blen(p) <= 8 for p in parts)

def test_split_text_cyrillic_byte_limit():
    # "ab cd" = 5 bytes; limit 4 -> splits after "ab"
    parts = main.split_text("ab cd", 4)
    assert parts[0] == "ab"
    assert parts[1] == "cd"

def test_split_text_no_space_hard_cut():
    # single long word, no spaces -> hard cut at byte boundary
    parts = main.split_text("abcdef", 3)
    assert all(main._blen(p) <= 3 for p in parts)

def test_split_text_cyrillic_no_word_cut():
    # Cyrillic text: each char 2 bytes, limit 10 bytes -> no word should be split
    text = "\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440"  # "Privet mir" = 19 bytes
    parts = main.split_text(text, 10)
    assert all(main._blen(p) <= 10 for p in parts)
    # verify all words are present and byte limit respected
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


# --- apply_replacements ---

def test_apply_replacements_basic():
    assert main.apply_replacements("hello world", {"world": "earth"}) == "hello earth"

def test_apply_replacements_multiple():
    assert main.apply_replacements("foo bar", {"foo": "baz", "bar": "qux"}) == "baz qux"

def test_apply_replacements_empty_dict():
    assert main.apply_replacements("hello", {}) == "hello"

def test_apply_replacements_no_match():
    assert main.apply_replacements("hello", {"xyz": "abc"}) == "hello"


# --- matches_any ---

def test_matches_any_hit():
    patterns = [re.compile("alert", re.IGNORECASE)]
    assert main.matches_any("ALERT level high", patterns) is True

def test_matches_any_miss():
    patterns = [re.compile("alert", re.IGNORECASE)]
    assert main.matches_any("all clear", patterns) is False

def test_matches_any_multiple_patterns():
    patterns = [re.compile("foo"), re.compile("bar")]
    assert main.matches_any("bar none", patterns) is True


# --- load_state / save_state ---

def test_load_state_missing(tmp_path):
    assert main.load_state(str(tmp_path / "no.txt")) == 0

def test_load_state_invalid(tmp_path):
    f = tmp_path / "state.txt"
    f.write_text("notanint")
    assert main.load_state(str(f)) == 0

def test_save_and_load_state(tmp_path):
    f = str(tmp_path / "state.txt")
    main.save_state(f, 12345)
    assert main.load_state(f) == 12345


# --- send_webhook ---

def test_send_webhook_success():
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    with patch("main.requests.post", return_value=mock_resp) as mock_post:
        status = main.send_webhook("http://example.com/wh", "msg", "#ch", "token123")
    assert status == 200
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"channel": "#ch", "message": "msg"}
    assert kwargs["headers"]["Authorization"] == "Bearer token123"

def test_send_webhook_no_token():
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    with patch("main.requests.post", return_value=mock_resp) as mock_post:
        main.send_webhook("http://example.com/wh", "msg", "#ch", "")
    _, kwargs = mock_post.call_args
    assert "Authorization" not in kwargs["headers"]

def test_send_webhook_raises_on_error():
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_resp.raise_for_status.side_effect = Exception("500")
    with patch("main.requests.post", return_value=mock_resp):
        with pytest.raises(Exception):
            main.send_webhook("http://example.com/wh", "msg", "#ch", "")


# --- process (integration) ---

def _make_cfg(tmp_path, state_file, webhook_url="http://wh/hook",
              max_length=200, transliterate="false", patterns=".*", replacements=None):
    cfg = configparser.ConfigParser()
    sections = {
        "source":  {"source_url": "http://src/posts", "interval_seconds": "60"},
        "filters": {"patterns": patterns},
        "webhook": {"webhook_url": webhook_url, "token": "", "channel": "#test",
                    "max_length": str(max_length), "part_delay_seconds": "0",
                    "transliterate": transliterate},
        "state":   {"state_file": state_file},
    }
    if replacements:
        sections["replacements"] = replacements
    cfg.read_dict(sections)
    return cfg

def _post(post_id, content, date="2026-01-01T10:00:00"):
    return {"id": post_id, "date": date,
            "content": {"rendered": f"<p>{content}</p>"},
            "title": {"rendered": ""}, "link": ""}

def test_process_sends_matching_post(tmp_path):
    state_file = str(tmp_path / "state.txt")
    cfg = _make_cfg(tmp_path, state_file)
    patterns = [re.compile(".*")]
    mock_resp = MagicMock()
    mock_resp.ok = True; mock_resp.status_code = 200
    posts_resp = MagicMock()
    posts_resp.json.return_value = [_post(1, "Hello world")]
    posts_resp.raise_for_status = MagicMock()
    with patch("main.requests.get", return_value=posts_resp), \
         patch("main.requests.post", return_value=mock_resp):
        main.process(cfg, patterns)
    assert main.load_state(state_file) == 1

def test_process_skips_old_post(tmp_path):
    state_file = str(tmp_path / "state.txt")
    main.save_state(state_file, 5)
    cfg = _make_cfg(tmp_path, state_file)
    patterns = [re.compile(".*")]
    posts_resp = MagicMock()
    posts_resp.json.return_value = [_post(5, "Old post")]
    posts_resp.raise_for_status = MagicMock()
    with patch("main.requests.get", return_value=posts_resp), \
         patch("main.requests.post") as mock_post:
        main.process(cfg, patterns)
    mock_post.assert_not_called()

def test_process_no_match_no_send(tmp_path):
    state_file = str(tmp_path / "state.txt")
    cfg = _make_cfg(tmp_path, state_file, patterns="NOMATCH")
    patterns = [re.compile("NOMATCH")]
    posts_resp = MagicMock()
    posts_resp.json.return_value = [_post(1, "Hello world")]
    posts_resp.raise_for_status = MagicMock()
    with patch("main.requests.get", return_value=posts_resp), \
         patch("main.requests.post") as mock_post:
        main.process(cfg, patterns)
    mock_post.assert_not_called()

def test_process_transliterates(tmp_path):
    state_file = str(tmp_path / "state.txt")
    cfg = _make_cfg(tmp_path, state_file, transliterate="true")
    patterns = [re.compile(".*")]
    mock_resp = MagicMock()
    mock_resp.ok = True; mock_resp.status_code = 200
    posts_resp = MagicMock()
    posts_resp.json.return_value = [_post(1, "\u041f\u0440\u0438\u0432\u0435\u0442")]
    posts_resp.raise_for_status = MagicMock()
    with patch("main.requests.get", return_value=posts_resp), \
         patch("main.requests.post", return_value=mock_resp) as mock_post:
        main.process(cfg, patterns)
    sent_message = mock_post.call_args[1]["json"]["message"]
    assert "\u041f" not in sent_message  # no Cyrillic
    assert "Privet" in sent_message

def test_process_splits_long_message(tmp_path):
    state_file = str(tmp_path / "state.txt")
    cfg = _make_cfg(tmp_path, state_file, max_length=20)
    patterns = [re.compile(".*")]
    mock_resp = MagicMock()
    mock_resp.ok = True; mock_resp.status_code = 200
    long_text = "word " * 10  # well over 20 bytes
    posts_resp = MagicMock()
    posts_resp.json.return_value = [_post(1, long_text)]
    posts_resp.raise_for_status = MagicMock()
    with patch("main.requests.get", return_value=posts_resp), \
         patch("main.requests.post", return_value=mock_resp) as mock_post:
        main.process(cfg, patterns)
    assert mock_post.call_count > 1

def test_process_applies_replacements(tmp_path):
    state_file = str(tmp_path / "state.txt")
    cfg = _make_cfg(tmp_path, state_file, replacements={"world": "earth"})
    patterns = [re.compile(".*")]
    mock_resp = MagicMock()
    mock_resp.ok = True; mock_resp.status_code = 200
    posts_resp = MagicMock()
    posts_resp.json.return_value = [_post(1, "Hello world")]
    posts_resp.raise_for_status = MagicMock()
    with patch("main.requests.get", return_value=posts_resp), \
         patch("main.requests.post", return_value=mock_resp) as mock_post:
        main.process(cfg, patterns, dict(cfg["replacements"]))
    sent_message = mock_post.call_args[1]["json"]["message"]
    assert "earth" in sent_message
    assert "world" not in sent_message


def test_process_replacements_before_translit(tmp_path):
    """Replacements run before transliteration, so replaced Latin text is not re-transliterated."""
    state_file = str(tmp_path / "state.txt")
    # configparser lowercases keys, so use lowercase Cyrillic as the replacement key
    cfg = _make_cfg(tmp_path, state_file, transliterate="true",
                    replacements={"privet": "Hi"})
    patterns = [re.compile(".*")]
    mock_resp = MagicMock()
    mock_resp.ok = True; mock_resp.status_code = 200
    posts_resp = MagicMock()
    # "privet" in content will be replaced to "Hi" before transliteration
    posts_resp.json.return_value = [_post(1, "privet mir")]
    posts_resp.raise_for_status = MagicMock()
    with patch("main.requests.get", return_value=posts_resp), \
         patch("main.requests.post", return_value=mock_resp) as mock_post:
        main.process(cfg, patterns, dict(cfg["replacements"]))
    sent_message = mock_post.call_args[1]["json"]["message"]
    assert "Hi" in sent_message
    assert "privet" not in sent_message


def test_process_fetch_error_no_state_change(tmp_path):
    state_file = str(tmp_path / "state.txt")
    cfg = _make_cfg(tmp_path, state_file)
    patterns = [re.compile(".*")]
    with patch("main.requests.get", side_effect=Exception("timeout")):
        main.process(cfg, patterns)
    assert main.load_state(state_file) == 0