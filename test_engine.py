import logging
from engine import Engine, EngineConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


cfg = EngineConfig(timeout=(3, 5), max_retries=2)
eng = Engine(cfg)

print("[*] Testing Engine...")
try:
    r = eng.get("https://httpbin.org/get")
    print(f"[+] Success! Status: {r.status_code}")
    print(f"[+] Our rotated UA: {r.json()['headers']['User-Agent']}")
    
    print("\n[*] Testing Rate Limit Backoff...")
    r2 = eng.get("https://httpbin.org/status/429")
    print(f"[+] Response: {r2.status_code}")
    
except Exception as e:
    print(f"[!] Error: {e}")
finally:
    eng.close()
