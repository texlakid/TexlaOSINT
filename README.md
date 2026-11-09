# 🕵️‍♂️ TexlaOSINT (v2.0)

A lightweight, modular, and mobile-first Open Source Intelligence (OSINT) framework written in Python.

Built and tested entirely on an Android device using Termux.

## ✨ Features

*   **Multi-Threaded Orchestrator (`main.py`):** Uses a thread-safe worker pool (`ThreadPoolExecutor`) to scan multiple profiles concurrently without race conditions.
*   **Anti-Ban Request Engine (`engine.py`):** Uses `requests.Session`, User-Agent rotation, and exponential backoff (honoring `Retry-After` headers) to survive Web Application Firewalls (WAFs) and rate limits.
*   **Intelligent Extraction (`extractor.py`):** A stateful parsing pipeline that extracts emails (including deobfuscated ones like `[at]` and `[dot]`) and pivots to  different social media platforms.
*   **Crash-Safe Streaming (`results.jsonl`):** Writes findings to disk in real-time. If the scan is interrupted, data is not lost.
*   **Resume Capability:** Saves `state.json` checkpoints. If your phone dies or you hit `Ctrl+C`, simply run with `--resume` to pick up exactly where you left off.
*   **Noise Reduction:** Features a 150+ word blacklist to ignore website navigation links (e.g., `/about`, `/login`, `/enterprise`) and prevents false positives.

## 📂 Architecture

```text
TexlaOSINT/
├── main.py              The Master Orchestrator (CLI, Threads, Queues)
├── engine.py            Anti-Ban HTTP Engine (WAF Evasion)
├── extractor.py         Regex Parsing & Noise Filtering
├── scanner.py           The Worker Class (Fetches individual profiles)
├── provider.json        The platform Regex Database
└── requirements.txt     Dependencies
🏆 Proof of Concept (Real-World Scan)

Running a scan on github:sindresorhus with --max-depth 2 successfully uncovered a real email address through recursive BFS pivoting:

1. Fetched github/sindresorhus
2. Pivoted to bluesky/sindresorhus.com
3. Pivoted to github/soml-lang
4. Successfully extracted sindresorhus@gmail.com

🚀 Quick Start (Termux / Linux)

1. Clone the repository:
   ```bash
   git clone https://github.com/texlakid/TexlaOSINT.git
   cd TexlaOSINT
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run a basic scan:
   ```bash
   python main.py --seed github:sindresorhus
   ```
4. Run an advanced multi-threaded scan (3 workers, depth 2):
   ```bash
   python main.py --seed github:torvalds --workers 3 --max-depth 2 -v
   ```

⚠️ Architectural Limitations

· JavaScript Walls: Platforms like Reddit, Instagram, and X (Twitter) heavily rely on client-side JavaScript rendering (React/Next.js). 
· Where it Excels: This tool is highly effective against static HTML platforms.

⚖️ Disclaimer

This tool is for educational purposes. Do not use against targets without explicit permission.

```
