# wordpress-to-webhook

Sidecar service: polls a WordPress REST API endpoint, filters posts by regular expressions, and forwards matching content to a webhook.

---

## config.ini reference

### [source]

| Parameter          | Type   | Default | Description |
|--------------------|--------|---------|-------------|
| `source_url`       | string | —       | GET endpoint to poll. Required. |
| `interval_seconds` | int    | `60`    | Polling interval in seconds. |

```ini
[source]
source_url = https://example.com/wp-json/wp/v2/posts?categories=10&per_page=5
interval_seconds = 60
```

---

### [filters]

| Parameter  | Type   | Description |
|------------|--------|-------------|
| `patterns` | string | Comma-separated regular expressions. A post is sent if **at least one** pattern matches. Applied to the original text **before** transliteration. Case-insensitive. |

```ini
# Send all posts
patterns = .*

# Match specific regions
patterns = Lipetsk,Voronezh,Kursk

# Using regexp groups
patterns = Lipetsk(aya|oj),BPLA,bespilotnik
```

---

### [webhook]

| Parameter             | Type   | Default    | Description |
|-----------------------|--------|------------|-------------|
| `webhook_url`         | string | —          | POST endpoint URL. Required. |
| `token`               | string | `""`       | Bearer token for the `Authorization` header. Omitted if empty. |
| `channel`             | string | `#general` | Value of the `channel` field in the request body. |
| `max_length`          | int    | `120`      | Maximum message part size **in bytes** (UTF-8). Cyrillic = 2 bytes/char, Latin = 1 byte/char. |
| `part_delay_seconds`  | float  | `7`        | Delay in seconds between sending consecutive parts of a split message. |
| `transliterate`       | bool   | `false`    | Transliterate Cyrillic to Latin before sending. |

```ini
[webhook]
webhook_url = http://you_endpoint_ip_or_dns:port/webhook
token = your-secret-token
channel = #warning
max_length = 120
part_delay_seconds = 7
transliterate = true
```

Request format sent to the webhook:

```
POST http://<host>:<port>/webhook
Content-Type: application/json
Authorization: Bearer <token>

{"channel": "#warning", "message": "20:09:00 - Na Marse otmenena opasnosti..."}
```

If the message exceeds `max_length` bytes it is split into parts on word boundaries.
Each part is sent as a separate request with a `part_delay_seconds` pause between them.

---

### [state]

| Parameter    | Type   | Default     | Description |
|--------------|--------|-------------|-------------|
| `state_file` | string | `state.txt` | File path for storing the last processed post ID. Prevents duplicate delivery after restart. |

```ini
[state]
state_file = state.txt
```

---

## Running

### Directly
```bash
pip install -r requirements.txt
python main.py
```

### Docker
```bash
docker build -t wordpress-to-webhook .
docker run -v $(pwd)/state.txt:/app/state.txt \
           -v $(pwd)/config.ini:/app/config.ini \
           wordpress-to-webhook
```

> Mount `config.ini` and `state.txt` from outside the container so the config can be changed without rebuilding the image and state is preserved across restarts.