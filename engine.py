from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Optional, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("engine")
logger.addHandler(logging.NullHandler())

DEFAULT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
)

RETRY_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

@dataclass(slots=True)
class EngineConfig:
    """Tunable behavior. Defaults are tuned for polite, survivable scraping."""
    timeout: float | tuple[float, float] = (10.0, 30.0)
    max_retries: int = 5
    allow_redirects: bool = True
    backoff_base: float = 0.5
    backoff_factor: float = 2.0
    backoff_max: float = 30.0
    retry_after_cap: float = 60.0
    jitter: float = 0.5
    pool_connections: int = 10
    pool_maxsize: int = 20
    user_agents: Sequence[str] = field(default_factory=lambda: DEFAULT_USER_AGENTS)
    ua_strategy: str = "rotate"
    default_headers: Mapping[str, str] = field(
        default_factory=lambda: {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    proxies: Mapping[str, str] = field(default_factory=dict)

def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Return seconds to wait from a Retry-After header, or None if unusable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())

class Engine:
    """Pooled, rotating, backoff-aware HTTP client."""
    
    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or EngineConfig()
        self._lock = threading.RLock()
        self._ua_index = random.randrange(len(self.config.user_agents) or 1)
        self.session = self._build_session()
        logger.debug("Engine initialized (pool=%d, retries=%d)", self.config.pool_maxsize, self.config.max_retries)

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.config.pool_connections,
            pool_maxsize=self.config.pool_maxsize,
            max_retries=Retry(total=0), # we own retry logic, not urllib3
            pool_block=False,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(dict(self.config.default_headers))
        session.headers["User-Agent"] = self._next_user_agent()
        if self.config.proxies:
            session.proxies.update(dict(self.config.proxies))
        return session

    def _next_user_agent(self) -> str:
        uas = list(self.config.user_agents) or list(DEFAULT_USER_AGENTS)
        with self._lock:
            if self.config.ua_strategy == "random":
                return random.choice(uas)
            ua = uas[self._ua_index % len(uas)]
            self._ua_index += 1
            return ua

    def rotate_identity(self, *, drop_cookies: bool = True) -> None:
        """Nuke session cookies and swap UA. Use when a target starts banning."""
        with self._lock:
            if drop_cookies:
                self.session.cookies.clear()
            self.session.headers["User-Agent"] = self._next_user_agent()
            logger.info("Identity rotated (cookies_cleared=%s)", drop_cookies)

    def set_proxies(self, proxies: Mapping[str, str]) -> None:
        self.session.proxies.clear()
        self.session.proxies.update(dict(proxies))

    def _backoff_delay(self, attempt: int, retry_after: Optional[float]) -> float:
        cfg = self.config
        if retry_after is not None:
            delay = min(retry_after, cfg.retry_after_cap)
        else:
            delay = cfg.backoff_base * (cfg.backoff_factor ** (attempt - 1))
            delay = min(delay, cfg.backoff_max)
        return delay + random.uniform(0.0, cfg.jitter)

    def request(
        self,
        method: str,
        url: str,
        *,
        retries: Optional[int] = None,
        stream: bool = False,
        allow_redirects: Optional[bool] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float | tuple[float, float]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        cfg = self.config
        max_attempts = (cfg.max_retries if retries is None else retries) + 1
        req_timeout = timeout if timeout is not None else cfg.timeout
        follow = cfg.allow_redirects if allow_redirects is None else allow_redirects
        base_headers = dict(headers or {})
        method = method.upper()

        last_exc: Optional[BaseException] = None

        for attempt in range(1, max_attempts + 1):
            call_headers = dict(base_headers)
            call_headers.setdefault("User-Agent", self._next_user_agent())

            try:
                response = self.session.request(
                    method,
                    url,
                    headers=call_headers,
                    timeout=req_timeout,
                    stream=stream,
                    allow_redirects=follow,
                    **kwargs,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    logger.error("%s %s failed after %d attempts: %s", method, url, attempt, exc)
                    raise
                delay = self._backoff_delay(attempt, None)
                logger.warning("%s %s -> %s, retry %d/%d in %.2fs", method, url, type(exc).__name__, attempt, max_attempts - 1, delay)
                time.sleep(delay)
                continue

            if response.status_code in RETRY_STATUSES and attempt < max_attempts:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                delay = self._backoff_delay(attempt, retry_after)
                logger.warning("%s %s -> HTTP %d, retry %d/%d in %.2fs%s", method, url, response.status_code, attempt, max_attempts - 1, delay, " (Retry-After honored)" if retry_after is not None else "")
                response.close()
                time.sleep(delay)
                continue

            return response

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"{method} {url}: exhausted retries without a response")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("HEAD", url, **kwargs)

    def close(self) -> None:
        with self._lock:
            try:
                self.session.close()
            except Exception:
                pass

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"Engine(pool={self.config.pool_maxsize}, retries={self.config.max_retries}, ua_strategy={self.config.ua_strategy!r})")
