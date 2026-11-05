# 🕵️‍♂️ TexlaOSINT

A lightweight, modular, and mobile-first Open Source Intelligence (OSINT) framework written in Python. 

Built and tested entirely on an Android device using Termux.

## ✨ Features

*   **Anti-Ban Request Engine (`engine.py`):** Uses `requests.Session`, User-Agent rotation, and exponential backoff (honoring `Retry-After` headers) to survive Web Application Firewalls (WAFs) and rate limits.
*   **Intelligent Extraction (`extractor.py`):** A stateful parsing pipeline that extracts emails (including deobfuscated ones like `[at]` and `[dot]`) and pivots to 9 different social media platforms.
*   **Recursive Scanning (`scanner.py`):** Uses a Breadth-First Search (BFS) algorithm to automatically pivot from one discovered profile to the next, up to a configurable depth.
*   **Noise Reduction:** Features a 150+ word blacklist to ignore website navigation links (e.g., `/about`, `/login`, `/enterprise`) and prevents false positives.

## 📂 Architecture

```text
texlaOSINT/
├── main.py             # The CLI interface (Routing)
├── engine.py           # Anti-Ban HTTP Engine (WAF Evasion)
├── extractor.py        # Regex Parsing & Noise Filtering
├── scanner.py          # Recursive BFS Orchestrator
└── provider.json       # The 9-platform Regex Database
```

🚀 Quick Start (Termux / Linux)

1. Clone the repository:
   ```bash
   git clone https://github.com/texlakid/TexlaOSINT.git
   cd TexlaOSINT
   ```
2. Install dependencies:
   ```bash
   pip install requests
   ```
3. Run a scan:
   ```bash
   python scanner.py github sindresorhus
   ```

⚠️ Architectural Limitations

· JavaScript Walls: Platforms like Reddit, Instagram, and X (Twitter) heavily rely on client-side JavaScript rendering (React/Next.js). Because this tool uses a lightweight requests engine to remain mobile-friendly, it parses the raw HTML shell 
· Where it Excels: This tool is highly effective against static HTML platforms 

⚖️ Disclaimer

This tool is for educational purposes . Do not use against targets without explicit permission.

```
