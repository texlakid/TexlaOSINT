from __future__ import annotations

import argparse
import csv
import inspect
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from engine import Engine, EngineConfig
from extractor import Extractor, Provider, load_providers

log = logging.getLogger("main")




@dataclass(frozen=True, slots=True)
class Node:
    provider: str
    handle: str
    depth: int = 0

    def key(self) -> tuple[str, str]:
        return (self.provider, self.handle)

    def __str__(self) -> str:
        return f"{self.provider}:{self.handle}"


@dataclass(slots=True)
class RunConfig:
    providers_path: Path
    seeds: list[Node]
    out_dir: Path
    workers: int = 4
    max_depth: int = 3
    max_nodes: int = 5000
    rate_seconds: float = 1.2
    engine_cfg: EngineConfig = field(default_factory=EngineConfig)
    resume_from: Optional[Path] = None
    scanner_module: str = "scanner"




class ScanOutcome:
    __slots__ = ("found", "status_code", "url", "html", "error")

    def __init__(
        self,
        found: bool,
        status_code: Optional[int] = None,
        url: str = "",
        html: str = "",
        error: str = "",
    ) -> None:
        self.found = found
        self.status_code = status_code
        self.url = url
        self.html = html
        self.error = error


def _normalize_scan_result(raw: Any) -> ScanOutcome:
    if raw is None:
        return ScanOutcome(found=False, error="scanner returned None")
    if isinstance(raw, bool):
        return ScanOutcome(found=raw)
    if isinstance(raw, dict):
        return ScanOutcome(
            found=bool(raw.get("found")),
            status_code=raw.get("status_code") or raw.get("status"),
            url=raw.get("url") or "",
            html=raw.get("html") or raw.get("body") or "",
            error=raw.get("error") or "",
        )
    found = getattr(raw, "found", None)
    if found is None:
        found = getattr(raw, "ok", None)
    if found is None:
        found = getattr(raw, "exists", None)
    response = getattr(raw, "response", None)
    html = getattr(raw, "html", None)
    status_code = getattr(raw, "status_code", None)
    url = getattr(raw, "url", "") or ""
    error = getattr(raw, "error", "") or ""
    if response is not None:
        html = html or getattr(response, "text", "")
        status_code = status_code or getattr(response, "status_code", None)
        url = url or getattr(response, "url", "") or ""
    if found is None:
        found = bool(html)
    return ScanOutcome(
        found=bool(found),
        status_code=status_code,
        url=url,
        html=html or "",
        error=error,
    )


class ScannerAdapter:
    def __init__(
        self,
        engine: Engine,
        providers: dict[str, Provider],
        module_name: str = "scanner",
    ) -> None:
        self.engine = engine
        self.providers = providers
        self._scanner: Any = None
        self._call: Any = None
        self._mode = "fallback"
        self._try_load(module_name)

    def _try_load(self, module_name: str) -> None:
        try:
            mod = __import__(module_name, fromlist=["Scanner"])
        except ImportError as exc:
            log.warning("scanner module %r not importable (%s); using fallback",
                        module_name, exc)
            return
        cls = getattr(mod, "Scanner", None)
        if cls is None:
            log.warning("scanner module has no `Scanner`; using fallback")
            return
        instance: Any = None
        for ctor in (
            lambda: cls(engine=self.engine, providers=self.providers),
            lambda: cls(self.engine, self.providers),
            lambda: cls(engine=self.engine),
        ):
            try:
                instance = ctor()
                break
            except TypeError:
                continue
            except Exception as exc:
                log.warning("scanner ctor failed (%s); using fallback", exc)
                return
        if instance is None:
            log.warning("could not instantiate Scanner; using fallback")
            return
        method = self._pick_method(instance)
        if method is None:
            log.warning("Scanner has no scan/check/probe/run; using fallback")
            return
        self._scanner = instance
        self._call = method
        self._mode = "scanner"
        log.info("scanner adapter: using %s.%s",
                 type(instance).__name__, method.__name__)

    @staticmethod
    def _pick_method(instance: Any) -> Optional[Any]:
        for name in ("scan", "check", "probe", "run"):
            fn = getattr(instance, name, None)
            if fn is None or not callable(fn):
                continue
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                return fn
            positional = [
                p for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            if len(positional) >= 2 or any(
                p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()
            ):
                return fn
        return None

    def scan(self, provider_name: str, handle: str) -> ScanOutcome:
        if self._mode == "scanner":
            for args in ((provider_name, handle), (handle, provider_name)):
                try:
                    raw = self._call(*args)
                    return _normalize_scan_result(raw)
                except TypeError:
                    continue
                except Exception as exc:
                    log.debug("scanner raised for %s:%s: %s",
                              provider_name, handle, exc)
                    return ScanOutcome(found=False, error=str(exc))
            return ScanOutcome(found=False, error="scanner signature mismatch")

        provider = self.providers.get(provider_name)
        if provider is None:
            return ScanOutcome(found=False, error=f"unknown provider {provider_name!r}")
        url = provider.profile_url(handle)
        try:
            response = self.engine.get(url)
        except Exception as exc:
            return ScanOutcome(found=False, url=url, error=str(exc))
        return _normalize_scan_result(response)

    @property
    def mode(self) -> str:
        return self._mode

class Runner:
    def __init__(self, cfg: RunConfig) -> None:
        self.cfg = cfg
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)

        self.providers = load_providers(cfg.providers_path)
        if not self.providers:
            raise SystemExit(f"no providers loaded from {cfg.providers_path}")

        self.queue: "queue.Queue[Node]" = queue.Queue()
        self.stop = threading.Event()

        self._lock = threading.Lock()
        self._visited: set[tuple[str, str]] = set()
        self._emails: dict[str, tuple[str, str]] = {}
        self._inflight = 0
        self._processed = 0
        self._errors = 0
        self._found = 0
        self._started_at = time.time()

        self._jsonl_path = cfg.out_dir / "results.jsonl"
        self._jsonl_lock = threading.Lock()
        self._jsonl_fh = self._jsonl_path.open("a", encoding="utf-8")

        self._state_path = cfg.out_dir / "state.json"

        self._load_state(cfg.resume_from or self._state_path)
        if cfg.seeds:
            for node in cfg.seeds:
                self._enqueue(node)

    def _load_state(self, path: Path) -> None:
        if not path.exists():
            return
        log.info("resuming from %s", path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("state load failed (%s); starting fresh", exc)
            return
        for provider, handle in data.get("visited", []):
            self._visited.add((provider, handle))
        for entry in data.get("pending", []):
            self._enqueue(Node(entry["provider"], entry["handle"], entry.get("depth", 0)))
        for email, src in data.get("emails", {}).items():
            self._emails[email] = (src[0], src[1])

    def _save_state(self) -> None:
        with self._lock:
            pending: list[dict] = []
            drained: list[Node] = []
            while True:
                try:
                    node = self.queue.get_nowait()
                except queue.Empty:
                    break
                drained.append(node)
                pending.append(
                    {"provider": node.provider, "handle": node.handle, "depth": node.depth}
                )
            for node in drained:
                self.queue.put(node)
            snapshot = {
                "visited": list(self._visited),
                "pending": pending,
                "emails": {k: list(v) for k, v in self._emails.items()},
                "processed": self._processed,
                "found": self._found,
                "errors": self._errors,
            }
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot), encoding="utf-8")
        tmp.replace(self._state_path)

    def _enqueue(self, node: Node) -> bool:
        if node.depth > self.cfg.max_depth:
            return False
        with self._lock:
            if node.key() in self._visited:
                return False
            self._visited.add(node.key())
            if len(self._visited) > self.cfg.max_nodes:
                log.warning("max_nodes=%d reached; refusing new node %s",
                            self.cfg.max_nodes, node)
                return False
        self.queue.put(node)
        return True

    def _worker(self, worker_id: int) -> None:
        engine = Engine(self.cfg.engine_cfg)
        extractor = Extractor(self.providers)
        scanner = ScannerAdapter(engine, self.providers, self.cfg.scanner_module)
        log.debug("worker %d ready (scanner=%s)", worker_id, scanner.mode)

        last_request = 0.0
        try:
            while not self.stop.is_set():
                try:
                    node = self.queue.get(timeout=0.5)
                except queue.Empty:
                    with self._lock:
                        if self._inflight == 0 and self.queue.empty():
                            return
                    continue

                with self._lock:
                    self._inflight += 1
                try:
                    wait = self.cfg.rate_seconds - (time.monotonic() - last_request)
                    if wait > 0:
                        time.sleep(wait)
                    last_request = time.monotonic()

                    self._process(node, engine, extractor, scanner)
                except Exception as exc:
                    log.exception("worker %d error on %s: %s", worker_id, node, exc)
                    with self._lock:
                        self._errors += 1
                finally:
                    with self._lock:
                        self._inflight -= 1
                    self.queue.task_done()
        finally:
            engine.close()

    def _process(
        self,
        node: Node,
        engine: Engine,
        extractor: Extractor,
        scanner: ScannerAdapter,
    ) -> None:
        if self.stop.is_set():
            return

        outcome = scanner.scan(node.provider, node.handle)
        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": node.provider,
            "handle": node.handle,
            "depth": node.depth,
            "found": outcome.found,
            "status_code": outcome.status_code,
            "url": outcome.url or _profile_url(self.providers, node.provider, node.handle),
            "error": outcome.error or None,
            "emails": [],
            "new_pivots": [],
        }

        with self._lock:
            self._processed += 1
            if outcome.found:
                self._found += 1

        if outcome.html:
            result = extractor.extract(
                outcome.html,
                base_url=record["url"],
                current_provider=node.provider,
                current_handle=node.handle,
            )
            new_emails: list[str] = []
            with self._lock:
                for email in result.emails:
                    if email in self._emails:
                        continue
                    self._emails[email] = (node.provider, node.handle)
                    new_emails.append(email)
            record["emails"] = new_emails

            if node.depth < self.cfg.max_depth:
                for provider, handle in result.iter_handles():
                    child = Node(provider, handle, node.depth + 1)
                    if self._enqueue(child):
                        record["new_pivots"].append([provider, handle])

            if result.stats:
                record["stats"] = result.stats

        self._write_jsonl(record)

        if self._processed % 50 == 0:
            self._save_state()
            elapsed = time.time() - self._started_at
            log.info(
                "progress: processed=%d found=%d errors=%d emails=%d queue=%d elapsed=%.1fs",
                self._processed, self._found, self._errors,
                len(self._emails), self.queue.qsize(), elapsed,
            )

    def _write_jsonl(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._jsonl_lock:
            self._jsonl_fh.write(line + "\n")
            self._jsonl_fh.flush()

    def run(self) -> int:
        log.info(
            "starting: seeds=%d workers=%d max_depth=%d max_nodes=%d out=%s",
            self.queue.qsize(), self.cfg.workers,
            self.cfg.max_depth, self.cfg.max_nodes, self.cfg.out_dir,
        )
        threads = [
            threading.Thread(target=self._worker, args=(i,), name=f"worker-{i}", daemon=True)
            for i in range(self.cfg.workers)
        ]
        for t in threads:
            t.start()
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            log.warning("SIGINT — draining workers")
            self.stop.set()
            for t in threads:
                t.join(timeout=10.0)
        self._finalize()
        return 0

    def _finalize(self) -> None:
        self._save_state()
        self._write_csvs()
        self._jsonl_fh.close()
        elapsed = time.time() - self._started_at
        log.info(
            "done: processed=%d found=%d errors=%d emails=%d visited=%d elapsed=%.1fs",
            self._processed, self._found, self._errors,
            len(self._emails), len(self._visited), elapsed,
        )
        print(
            f"\nProcessed : {self._processed}\n"
            f"Found     : {self._found}\n"
            f"Errors    : {self._errors}\n"
            f"Emails    : {len(self._emails)}\n"
            f"Output    : {self.cfg.out_dir}\n"
        )

    def _write_csvs(self) -> None:
        profiles_path = self.cfg.out_dir / "profiles.csv"
        emails_path = self.cfg.out_dir / "emails.csv"
        with self._lock:
            visited = sorted(self._visited)
            emails = dict(self._emails)
        with profiles_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["provider", "handle", "url"])
            for provider, handle in visited:
                w.writerow([
                    provider, handle,
                    _profile_url(self.providers, provider, handle),
                ])
        with emails_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["email", "source_provider", "source_handle"])
            for email, (provider, handle) in sorted(emails.items()):
                w.writerow([email, provider, handle])


def _profile_url(providers: dict[str, Provider], provider: str, handle: str) -> str:
    p = providers.get(provider)
    return p.profile_url(handle) if p else ""


def _parse_seed(arg: str) -> Node:
    if ":" in arg:
        provider, handle = arg.split(":", 1)
    else:
        parts = arg.split()
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"Invalid seed format: '{arg}'. Use 'provider:handle' or 'provider handle'."
            )
        provider, handle = parts

    provider = provider.strip().lower()
    handle = handle.strip()

    if not provider or not handle:
        raise argparse.ArgumentTypeError(
            f"Invalid seed format: '{arg}'. Provider and handle cannot be empty."
        )

    return Node(provider=provider, handle=handle, depth=0)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="TexlaOSINT",
        description="Recursive OSINT pivot scanner (mobile-first, multi-threaded).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument(
        "--seed", action="append", type=_parse_seed, default=[],
        help="Starting node. Repeatable. Example: --seed github:octocat"
    )
    p.add_argument(
        "--seed-file", type=Path,
        help="File containing seeds (one per line, format: provider:handle)."
    )
    p.add_argument(
        "--out", type=Path, default=Path("runs/latest"),
        help="Directory to save results and state."
    )
    p.add_argument(
        "--workers", type=int, default=4,
        help="Number of concurrent threads."
    )
    p.add_argument(
        "--max-depth", type=int, default=3,
        help="Maximum recursive depth."
    )
    p.add_argument(
        "--max-nodes", type=int, default=500,
        help="Maximum total profiles to visit before stopping."
    )
    p.add_argument(
        "--rate", type=float, default=1.2,
        help="Seconds to wait between requests per worker."
    )
    p.add_argument(
        "--resume", action="store_true",
        help="Resume from an existing state.json in the --out directory."
    )
    p.add_argument(
        "--scanner-module", type=str, default="scanner",
        help="Name of the module containing the Scanner class."
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase log verbosity (-v info, -vv debug)."
    )

    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    log_level = logging.WARNING
    if args.verbose == 1:
        log_level = logging.INFO
    elif args.verbose >= 2:
        log_level = logging.DEBUG

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    seeds = list(args.seed)
    if args.seed_file:
        if not args.seed_file.exists():
            log.error(f"Seed file not found: {args.seed_file}")
            return 1
        try:
            for line in args.seed_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                seeds.append(_parse_seed(line))
        except Exception as e:
            log.error(f"Failed to read seed file: {e}")
            return 1

    if not seeds:
        log.error("No seeds provided. Use --seed or --seed-file.")
        return 1

    engine_cfg = EngineConfig(
        timeout=(10.0, 30.0),
        max_retries=3,
        ua_strategy="rotate"
    )

    resume_path = None
    if args.resume:
        resume_path = args.out / "state.json"
        if not resume_path.exists():
            log.warning(f"Resume requested but no state found at {resume_path}. Starting fresh.")

    cfg = RunConfig(
        providers_path=Path("provider.json"),
        seeds=seeds,
        out_dir=args.out,
        workers=args.workers,
        max_depth=args.max_depth,
        max_nodes=args.max_nodes,
        rate_seconds=args.rate,
        engine_cfg=engine_cfg,
        resume_from=resume_path,
        scanner_module=args.scanner_module,
    )

    try:
        runner = Runner(cfg)
        return runner.run()
    except SystemExit as e:
        log.error(f"Startup failed: {e}")
        return 1
    except Exception as e:
        log.exception(f"Fatal error in runner: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

