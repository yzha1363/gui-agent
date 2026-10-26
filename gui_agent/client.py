"""One bounded HTTP request to a local or remote chat-completions service."""

from dataclasses import dataclass, field
import json
from http.client import HTTPException
import math
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from gui_agent.contracts import AgentError, strict_json


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    api_key: str = field(default='', repr=False)
    timeout: float = 30
    max_tokens: int = 1024

    def __post_init__(self):
        try:
            url = urlsplit(self.base_url)
            _ = url.port
        except (ValueError, TypeError):
            raise AgentError('invalid_config', 'Invalid service URL.') from None
        local = url.hostname in ('localhost', '127.0.0.1', '::1')
        if (not url.hostname or url.username or url.password or url.query or url.fragment or
            (url.scheme != 'https' and not (url.scheme == 'http' and local))):
            raise AgentError('invalid_config', 'Use HTTPS, or loopback HTTP, without URL credentials/query/fragment.')
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 200:
            raise AgentError('invalid_config', 'Provide a nonempty model name.')
        if not isinstance(self.api_key, str) or any(ord(char) < 32 for char in self.api_key):
            raise AgentError('invalid_config', 'Invalid API key format.')
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (float, int)) or not math.isfinite(self.timeout) or not 0 < self.timeout <= 120:
            raise AgentError('invalid_config', 'timeout must be finite in (0,120].')
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 4096:
            raise AgentError('invalid_config', 'max_tokens must be an integer in 1..4096.')

    @property
    def endpoint(self):
        return self.base_url.rstrip('/') + '/chat/completions'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatClient:
    kind = 'http'

    def __init__(self, config, opener=None):
        self.config = config
        self.opener = opener or build_opener(NoRedirect())
        self.attempts = 0

    def invoke(self, messages):
        body = json.dumps({'model': self.config.model, 'messages': messages, 'stream': False,
                           'temperature': 0, 'max_tokens': self.config.max_tokens},
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if self.config.api_key:
            headers['Authorization'] = 'Bearer ' + self.config.api_key
        request = Request(self.config.endpoint, data=body, headers=headers, method='POST')
        self.attempts += 1
        try:
            with self.opener.open(request, timeout=self.config.timeout) as response:
                if response.status != 200:
                    raise AgentError('http_error', 'Service did not return HTTP 200.')
                data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise AgentError('response_too_large', 'Service response exceeds 2 MiB.')
        except HTTPError as exc:
            code = 'authentication_failed' if exc.code in (401, 403) else 'rate_limited' if exc.code == 429 else 'http_error'
            exc.close()
            raise AgentError(code, f'Service returned HTTP {exc.code}; request was not retried.') from None
        except (TimeoutError, socket.timeout):
            raise AgentError('model_timeout', 'Service request timed out; no automatic retry.') from None
        except URLError as exc:
            code = 'model_timeout' if isinstance(exc.reason, TimeoutError) else 'connection_failed'
            raise AgentError(code, 'Cannot reach the configured model service; no automatic retry.') from None
        except OSError:
            raise AgentError('connection_failed', 'Model service I/O failed; no automatic retry.') from None
        except HTTPException:
            raise AgentError('connection_failed', 'Incomplete HTTP transport response; no automatic retry.') from None
        try:
            envelope = strict_json(data.decode('utf-8'))
            choices = envelope['choices']
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError()
            choice = choices[0]
            message = choice['message']
            if message.get('refusal') or message.get('tool_calls'):
                raise AgentError('model_refusal', 'Refusal/tool-call response cannot become a proposal.')
            if choice['finish_reason'] != 'stop':
                raise AgentError('incomplete_response', 'Model response did not finish normally.')
            if not isinstance(message['content'], str):
                raise ValueError()
        except AgentError:
            raise
        except (UnicodeError, KeyError, TypeError, ValueError, AttributeError):
            raise AgentError('invalid_response', 'Invalid chat-completions response envelope.') from None
        usage = envelope.get('usage', {})
        usage = {k: v for k, v in usage.items() if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and type(v) is int and v >= 0} if isinstance(usage, dict) else {}
        return {'content': message['content'], 'usage': usage, 'model': self.config.model}


class ReplayClient:
    """Explicit deterministic fixture; never represented as model inference."""
    kind = 'replay'

    def __init__(self, content):
        self.content = content
        self.attempts = 0

    def invoke(self, messages):
        self.attempts += 1
        return {'content': self.content, 'usage': {}, 'model': 'offline-fixture'}
