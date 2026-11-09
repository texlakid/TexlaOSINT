import logging
from typing import Any, Mapping


from engine import Engine
from extractor import Provider

LOG = logging.getLogger("scanner")

class Scanner:
    """
    A lightweight worker class that fetches a single profile.
    The heavy lifting (queues, threads, extraction) is handled by main.py.
    """
    def __init__(self, engine: Engine, providers: Mapping[str, Provider], **kwargs: Any):
        self.engine = engine
        self.providers = providers

    def scan(self, provider_name: str, handle: str) -> dict:
        """
        Fetches the profile URL for the given provider and handle.
        Returns a dictionary that main.py's ScannerAdapter can parse.
        """
        provider = self.providers.get(provider_name)
        if not provider:
            return {
                "found": False,
                "error": f"Unknown provider: {provider_name}"
            }

        
        url = provider.profile_url(handle)
        
        try:
        
            response = self.engine.get(url)
            
            if response.status_code == 200:
                return {
                    "found": True,
                    "status_code": 200,
                    "url": url,
                    "html": response.text
                }
            else:
                return {
                    "found": False,
                    "status_code": response.status_code,
                    "url": url,
                    "html": ""
                }
                
        except Exception as exc:
            LOG.debug(f"Fetch failed for {url}: {exc}")
            return {
                "found": False,
                "url": url,
                "error": str(exc)
            }
