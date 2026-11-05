import json
import sys
from collections import deque
from engine import Engine
from extractor import load_providers, Extractor


C_GREEN = '\033[92m'
C_CYAN = '\033[96m'
C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_RESET = '\033[0m'

def run_scanner(seed_provider: str, seed_handle: str, max_depth: int = 2, max_queue: int = 50):
    print(f"{C_CYAN}[*] Initializing TexlaOSINT Engine...{C_RESET}")
    providers = load_providers("provider.json")
    extractor = Extractor(providers)
    engine = Engine()

 
    queue = deque([(seed_provider, seed_handle, 0)])
    visited = set()
    
    all_emails = set()
    all_pivots = []

    print(f"{C_CYAN}[*] Target: {seed_provider}/{seed_handle}{C_RESET}")
    print(f"{C_CYAN}[*] Max Depth: {max_depth} | Max Nodes: {max_queue}\n{C_RESET}")

    while queue and len(visited) < max_queue:
        provider, handle, depth = queue.popleft()
        
        if (provider, handle) in visited:
            continue
        visited.add((provider, handle))

        url = providers[provider].profile_url(handle)
        print(f"[{depth}] Fetching: {url}")
        
        try:
            response = engine.get(url)
        except Exception as exc:
            print(f"    {C_RED}[skip] Connection failed: {exc}{C_RESET}")
            continue
            
        if response.status_code != 200:
            print(f"    {C_YELLOW}[skip] HTTP {response.status_code}{C_RESET}")
            continue

      
        result = extractor.extract_from_response(
            response, current_provider=provider, current_handle=handle,
        )
        
     
        for email in result.emails:
            print(f"    {C_GREEN}[!] EMAIL FOUND: {email}{C_RESET}")
            all_emails.add(email)

        # 2. Queue New Pivots
        if depth < max_depth:
            for new_provider, new_handle in result.iter_handles():
                if (new_provider, new_handle) not in visited:
                    print(f"    {C_CYAN}[pivot] -> {new_provider}/{new_handle}{C_RESET}")
                    queue.append((new_provider, new_handle, depth + 1))
                    all_pivots.append({
                        "source": f"{provider}/{handle}",
                        "target": f"{new_provider}/{new_handle}"
                    })

    
    output_data = {
        "seed": f"{seed_provider}/{seed_handle}",
        "emails_found": list(all_emails),
        "pivots_found": all_pivots,
        "total_nodes_scanned": len(visited)
    }
    
    filename = f"osint_{seed_handle}.json"
    with open(filename, "w") as f:
        json.dump(output_data, f, indent=4)

    print(f"\n{C_GREEN}[*] Scan Complete. Scanned {len(visited)} nodes.{C_RESET}")
    print(f"{C_GREEN}[*] Total Emails Found: {len(all_emails)}{C_RESET}")
    print(f"{C_GREEN}[*] Results saved to {filename}{C_RESET}")
    engine.close()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scanner.py <provider> <handle>")
        print("Example: python scanner.py github torvalds")
        sys.exit(1)
        
   
    run_scanner(sys.argv[1].lower(), sys.argv[2].lower(), max_depth=1)
