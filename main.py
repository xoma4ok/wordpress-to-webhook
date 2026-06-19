import re
import sys
import time
import logging
import requests
import configparser
from html.parser import HTMLParser
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Cyrillic-to-Latin transliteration table (lowercase and uppercase)
_TRANSLIT = {
    '\u0430':'a','\u0431':'b','\u0432':'v','\u0433':'g','\u0434':'d',
    '\u0435':'e','\u0451':'yo','\u0436':'zh','\u0437':'z','\u0438':'i',
    '\u0439':'j','\u043a':'k','\u043b':'l','\u043c':'m','\u043d':'n',
    '\u043e':'o','\u043f':'p','\u0440':'r','\u0441':'s','\u0442':'t',
    '\u0443':'u','\u0444':'f','\u0445':'kh','\u0446':'ts','\u0447':'ch',
    '\u0448':'sh','\u0449':'sch','\u044a':'','\u044b':'y','\u044c':'',
    '\u044d':'e','\u044e':'yu','\u044f':'ya',
    '\u0410':'A','\u0411':'B','\u0412':'V','\u0413':'G','\u0414':'D',
    '\u0415':'E','\u0401':'Yo','\u0416':'Zh','\u0417':'Z','\u0418':'I',
    '\u0419':'J','\u041a':'K','\u041b':'L','\u041c':'M','\u041d':'N',
    '\u041e':'O','\u041f':'P','\u0420':'R','\u0421':'S','\u0422':'T',
    '\u0423':'U','\u0424':'F','\u0425':'Kh','\u0426':'Ts','\u0427':'Ch',
    '\u0428':'Sh','\u0429':'Sch','\u042a':'','\u042b':'Y','\u042c':'',
    '\u042d':'E','\u042e':'Yu','\u042f':'Ya',
}

def translit(text: str) -> str:
    """Transliterate Cyrillic characters to Latin using the _TRANSLIT table."""
    return ''.join(_TRANSLIT.get(c, c) for c in text)


class _HTMLStripper(HTMLParser):
    """Minimal HTML parser that collects only text nodes."""
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(html: str) -> str:
    """Strip HTML tags and return plain text. Tolerates malformed markup."""
    p = _HTMLStripper()
    try:
        p.feed(html)
    except Exception as e:
        log.warning("HTML parse warning: %s", e)
    return " ".join(p.parts).strip()


def _blen(text: str) -> int:
    """Return the UTF-8 byte length of a string."""
    return len(text.encode("utf-8"))


def split_text(text: str, max_len: int) -> list:
    """Split text into parts where each part does not exceed max_len bytes (UTF-8).
    Splits are made on word boundaries; hard-cuts only if a single word exceeds the limit."""
    if _blen(text) <= max_len:
        return [text]
    parts = []
    while text:
        if _blen(text) <= max_len:
            parts.append(text)
            break
        # Binary search for the rightmost character position within the byte limit
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _blen(text[:mid]) <= max_len:
                lo = mid
            else:
                hi = mid - 1
        # Prefer splitting on the last space; fall back to hard cut if no space found
        cut = text.rfind(" ", 0, lo) if " " in text[:lo] else lo
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    return parts


def load_state(path: str) -> int:
    """Read the last processed post ID from the state file. Returns 0 if missing or invalid."""
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return 0


def save_state(path: str, post_id: int):
    """Persist the last processed post ID to the state file."""
    try:
        Path(path).write_text(str(post_id))
    except Exception as e:
        log.error("Failed to save state to %s: %s", path, e)


def matches_any(text: str, patterns: list) -> bool:
    """Return True if text matches at least one compiled regexp pattern."""
    return any(p.search(text) for p in patterns)


def send_webhook(webhook_url: str, message: str, channel: str, token: str, timeout: int = 10):
    """POST a JSON payload to the webhook. Adds Bearer auth header if token is set."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"channel": channel, "message": message}
    wh = requests.post(webhook_url, json=payload, headers=headers, timeout=timeout)
    if not wh.ok:
        log.error("Webhook responded %d: %s", wh.status_code, wh.text[:200])
    wh.raise_for_status()
    return wh.status_code


def load_config(path: str = "config.ini") -> configparser.ConfigParser:
    """Load and parse config.ini. Exits with error if file is missing or malformed."""
    cfg = configparser.ConfigParser()
    if not Path(path).exists():
        log.error("Config file not found: %s", path)
        sys.exit(1)
    try:
        cfg.read(path, encoding="utf-8")
    except configparser.Error as e:
        log.error("Failed to parse config: %s", e)
        sys.exit(1)
    return cfg


def process(cfg: configparser.ConfigParser, patterns: list):
    """Single poll cycle: fetch posts, filter, optionally transliterate, split and send."""
    try:
        url = cfg["source"]["source_url"]
        webhook_url = cfg["webhook"]["webhook_url"]
        state_file = cfg["state"]["state_file"]
        token = cfg["webhook"].get("token", "").strip()
        channel = cfg["webhook"].get("channel", "#general")
        max_len = int(cfg["webhook"].get("max_length", 120))
        part_delay = float(cfg["webhook"].get("part_delay_seconds", 7))
        do_translit = cfg["webhook"].getboolean("transliterate", fallback=False)
    except (KeyError, ValueError) as e:
        log.error("Config error: %s", e)
        return

    last_id = load_state(state_file)
    log.info("Request: %s (last_id=%d)", url, last_id)

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        posts = resp.json()
    except Exception as e:
        log.error("Fetch error: %s", e)
        return

    if not isinstance(posts, list):
        log.error("Unexpected response format: %s", type(posts).__name__)
        return

    for post in posts:
        post_id = post.get("id", 0)
        # Skip posts already processed in a previous cycle
        if post_id <= last_id:
            log.debug("Skip post id=%d (already processed)", post_id)
            continue

        # Extract plain text from content.rendered and prepend time from date field
        raw_html = post.get("content", {}).get("rendered", "")
        text = strip_html(raw_html)
        date_val = post.get("date", "")
        if "T" in date_val:
            text = date_val.split("T")[1] + " - " + text

        if not matches_any(text, patterns):
            log.info("Post id=%d did not match filters (text: %.60s...)", post_id, text)
        else:
            if do_translit:
                text = translit(text)
                log.debug("Post id=%d transliterated", post_id)
            # Split into byte-limited parts and send each to the webhook
            parts = split_text(text, max_len)
            log.info("Post id=%d matched, sending %d part(s) to %s, channel=%s",
                     post_id, len(parts), webhook_url, channel)
            failed = False
            for i, part in enumerate(parts):
                log.info("Post id=%d sending part %d/%d (%d bytes): %s",
                         post_id, i + 1, len(parts), _blen(part), part)
                try:
                    status = send_webhook(webhook_url, part, channel, token)
                    log.info("Post id=%d part %d/%d sent (status %d)", post_id, i + 1, len(parts), status)
                except Exception as e:
                    log.error("Webhook error for post id=%d part %d: %s", post_id, i + 1, e)
                    failed = True
                    break
                # Pause between parts to avoid flooding the webhook
                if i < len(parts) - 1:
                    time.sleep(part_delay)
            if failed:
                # Do not advance state so the post is retried next cycle
                continue

        save_state(state_file, post_id)
        last_id = post_id


def main():
    cfg = load_config()

    # Compile filter patterns once at startup
    try:
        patterns = [
            re.compile(s.strip(), re.IGNORECASE)
            for s in cfg["filters"]["patterns"].split(",")
            if s.strip()
        ]
    except (KeyError, re.error) as e:
        log.error("Filter config error: %s", e)
        sys.exit(1)

    log.info("Loaded %d filter pattern(s): %s", len(patterns), [p.pattern for p in patterns])

    try:
        interval = int(cfg["source"].get("interval_seconds", 60))
    except ValueError as e:
        log.error("Invalid interval_seconds in config: %s", e)
        sys.exit(1)

    log.info("Sidecar started, interval=%ds", interval)

    try:
        while True:
            process(cfg, patterns)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Sidecar stopped")


if __name__ == "__main__":
    main()