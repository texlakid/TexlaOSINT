from __future__ import annotations

import html as _html
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import unquote, urljoin

logger = logging.getLogger("extractor")
logger.addHandler(logging.NullHandler())



EMAIL_RE = re.compile(
    r"(?<![\w.+-])"
    r"([A-Za-z0-9._%+-]{1,64})"
    r"@"
    r"([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)"
    r"(?![\w-])",
    re.IGNORECASE,
)

MAILTO_RE = re.compile(r"""href\s*=\s*["']mailto:([^"'\s]+)""", re.IGNORECASE)


_BRACKET_AT = re.compile(r"\s*(?:\[|\(|<)\s*at\s*(?:\]|\)|>)\s*", re.IGNORECASE)
_BRACKET_DOT = re.compile(r"\s*(?:\[|\(|<)\s*dot\s*(?:\]|\)|>)\s*", re.IGNORECASE)
_BARE_AT = re.compile(
    r"\b([A-Za-z0-9._%+-]{1,64})\s+at\s+([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b",
    re.IGNORECASE,
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")



EMAIL_BLOCK_DOMAINS: frozenset[str] = frozenset({
    "example.com", "example.org", "example.net",
    "yourdomain.com", "your-domain.com", "domain.com",
    "email.com", "email.tld", "test.com",
    "sentry.io", "wixpress.com",
    
    
    "mastodon.social", "mastodon.online", "mstdn.social", 
    "fosstodon.org", "hachyderm.io", "infosec.exchange"
})

EMAIL_BLOCK_LOCALS: frozenset[str] = frozenset({
    "you", "your", "yourname", "name", "user", "username", "test", "example",
    "email", "mail", "noreply", "no-reply", "donotreply", "do-not-reply",
    "postmaster", "webmaster", "abuse",
})

EMAIL_BLOCK_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".css", ".js", ".map", ".json", ".xml", ".pdf", ".woff", ".woff2", ".ttf",
})


USERNAME_NOISE: frozenset[str] = frozenset({
    "about", "account", "accounts", "admin", "api", "app", "apps", "auth",
    "blog", "business", "careers", "cdn", "checkout", "community", "company",
    "contact", "cookies", "copyright", "css", "dashboard", "developer",
    "developers", "discover", "docs", "download", "downloads", "edit",
    "email", "embed", "explore", "faq", "features", "feed", "feedback",
    "forgot", "help", "home", "i18n", "images", "img", "intent", "jobs",
    "join", "legal", "login", "logout", "marketplace", "media", "messages",
    "mobile", "notifications", "oauth", "password", "photos", "policy",
    "press", "pricing", "privacy", "product", "products", "profile", "public",
    "register", "reset", "robots", "search", "security", "settings", "share",
    "signin", "signout", "signup", "sitemap", "static", "status", "support",
    "terms", "tos", "trending", "upload", "user", "users", "verify", "video",
    "videos", "web", "welcome", "www",
    
   
    "solutions", "enterprise", "open-source", "resources", "orgs", 
    "customer-stories", "organizations", "team", "events", "platform", 
    "roadmap-webinar-series", "sponsors", "readme", "newsletter", 
    "copilot", "skills", "github", "site", "topics", "collections", 
    "marketplace", "partners", "why-github", "trust-center", "site-map",
    "code-of-conduct", "git-guides", "readme", "security", "pricing"
})



@dataclass(frozen=True, slots=True)
class Provider:
    """One target platform. Populated from provider.json."""

    name: str
    url_template: str                     
    regex_url: re.Pattern[str]             
    username_re: re.Pattern[str] = USERNAME_RE

    def profile_url(self, handle: str) -> str:
        try:
            return self.url_template.format(username=handle)
        except (KeyError, IndexError):
            return self.url_template


def load_providers(path: str | Path) -> dict[str, Provider]:
    raw: dict[str, Any]
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    registry: dict[str, Provider] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        rx_src = spec.get("regexUrl") or spec.get("regex_url") or spec.get("regex")
        if not rx_src:
            logger.warning("provider %r missing regexUrl; skipped", name)
            continue
        try:
            compiled = re.compile(rx_src, re.IGNORECASE)
        except re.error as exc:
            logger.error("provider %r bad regexUrl: %s; skipped", name, exc)
            continue

        user_src = spec.get("usernameRegex") or spec.get("username_regex")
        try:
            user_re = re.compile(user_src) if user_src else USERNAME_RE
        except re.error as exc:
            logger.error("provider %r bad usernameRegex: %s; using default", name, exc)
            user_re = USERNAME_RE

        registry[name.lower()] = Provider(
            name=name,
            url_template=spec.get("url") or spec.get("urlTemplate") or "",
            regex_url=compiled,
            username_re=user_re,
        )
    return registry



@dataclass(slots=True)
class ExtractionResult:
    """New findings only — anything already seen is excluded."""

    emails: set[str] = field(default_factory=set)
    handles: dict[str, set[str]] = field(default_factory=dict)   
    urls: dict[str, set[str]] = field(default_factory=dict)      
    stats: dict[str, int] = field(default_factory=dict)          

    def add_handle(self, provider: str, handle: str, url: Optional[str] = None) -> bool:
        bucket = self.handles.setdefault(provider, set())
        if handle in bucket:
            return False
        bucket.add(handle)
        if url:
            self.urls.setdefault(provider, set()).add(url)
        return True

    def iter_handles(self) -> Iterator[tuple[str, str]]:
        for provider, handles in self.handles.items():
            for handle in handles:
                yield provider, handle

    def is_empty(self) -> bool:
        return not self.emails and not self.handles

    def __bool__(self) -> bool:
        return not self.is_empty()



class Extractor:
    def __init__(
        self,
        providers: Mapping[str, Provider],
        *,
        email_block_locals: Optional[Mapping[str, str] | set[str]] = None,
        email_block_domains: Optional[set[str]] = None,
    ) -> None:
        self.providers: dict[str, Provider] = dict(providers)
        self._block_locals = frozenset(email_block_locals or EMAIL_BLOCK_LOCALS)
        self._block_domains = frozenset(email_block_domains or EMAIL_BLOCK_DOMAINS)
        self._seen_emails: set[str] = set()
        self._seen_handles: set[tuple[str, str]] = set()

    def extract(
        self,
        html_text: str,
        *,
        base_url: str = "",
        current_provider: str = "",
        current_handle: str = "",
    ) -> ExtractionResult:
        result = ExtractionResult()
        if not html_text:
            return result

        self._extract_emails(html_text, result)
        self._extract_social(
            html_text,
            result,
            base_url=base_url,
            current_provider=current_provider.lower(),
            current_handle=current_handle.lower(),
        )
        return result

    def extract_from_response(
        self,
        response: Any,
        *,
        current_provider: str = "",
        current_handle: str = "",
    ) -> ExtractionResult:
        if response is None:
            return ExtractionResult()
        try:
            text = response.text
        except Exception as exc:
            logger.debug("response.text failed: %s", exc)
            return ExtractionResult()
        return self.extract(
            text,
            base_url=str(getattr(response, "url", "") or ""),
            current_provider=current_provider,
            current_handle=current_handle,
        )

    def reset(self) -> None:
        self._seen_emails.clear()
        self._seen_handles.clear()

    def _extract_emails(self, raw_html: str, result: ExtractionResult) -> None:
        text = _html.unescape(raw_html)
        found: set[str] = set()

        for match in MAILTO_RE.finditer(text):
            target = unquote(match.group(1)).split("?", 1)[0]
            for candidate in re.split(r"[,;]", target):
                candidate = candidate.strip().lower()
                if candidate and self._valid_email(candidate):
                    found.add(candidate)

        deob = self._deobfuscate(text)
        for match in EMAIL_RE.finditer(deob):
            addr = f"{match.group(1)}@{match.group(2)}".lower()
            if self._valid_email(addr):
                found.add(addr)

        result.stats["emails_raw"] = len(found)
        fresh = found - self._seen_emails
        self._seen_emails.update(fresh)
        result.emails.update(fresh)

    def _deobfuscate(self, text: str) -> str:
        text = _BRACKET_AT.sub("@", text)
        text = _BRACKET_DOT.sub(".", text)
        text = _BARE_AT.sub(r"\1@\2", text)
        return text

    def _valid_email(self, addr: str) -> bool:
        if addr.count("@") != 1:
            return False
        local, domain = addr.rsplit("@", 1)
        if not local or not domain:
            return False
        if len(addr) > 254 or len(local) > 64:
            return False
        if not re.fullmatch(r"[a-z0-9._%+-]+", local):
            return False
        
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}", domain):
            return False
            
        if "." not in domain:
            return False
        for ext in EMAIL_BLOCK_EXTS:
            if domain.endswith(ext) or local.endswith(ext):
                return False
        if domain in self._block_domains:
            return False
        if local in self._block_locals:
            return False
        return True

    def _extract_social(
        self,
        raw_html: str,
        result: ExtractionResult,
        *,
        base_url: str,
        current_provider: str,
        current_handle: str,
    ) -> None:
        text = _html.unescape(raw_html)

        for pkey, provider in self.providers.items():
            raw_hits = 0
            for match in provider.regex_url.finditer(text):
                raw_hits += 1
                handle = self._normalize_handle(
                    match.group(1) if match.groups() else match.group(0),
                    provider.username_re,
                )
                if handle is None:
                    continue
                
                if pkey == current_provider and handle.lower() == current_handle:
                    continue
                if (pkey, handle) in self._seen_handles:
                    continue
                url = provider.profile_url(handle)
                if base_url and url and "://" not in url:
                    url = urljoin(base_url, url)
                if result.add_handle(pkey, handle, url or None):
                    self._seen_handles.add((pkey, handle))

            if raw_hits:
                result.stats[f"{pkey}_raw"] = raw_hits

    def _normalize_handle(self, raw: str, username_re: re.Pattern[str]) -> Optional[str]:
        if not raw:
            return None
        handle = raw.strip().strip("/")
        handle = handle.split("?", 1)[0].split("#", 1)[0]
        handle = unquote(handle).strip()
        if not handle:
            return None
        if handle.lower() in USERNAME_NOISE:
            return None
        if not username_re.fullmatch(handle):
            return None
        return handle
