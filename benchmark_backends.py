#!/usr/bin/env python3
"""Benchmark script to compare LLM backends."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from assistant.llm.backends import (
    LLMBackend,
    InferenceStats,
    create_backend,
    ChatMessage,
)


TEST_PROMPTS = [
    "Explain Python async/await in 3 sentences.",
    "Write a quicksort implementation in Python.",
    "What are the differences between lists and tuples in Python?",
    "How does garbage collection work in Python?",
    "Explain decorators with a practical example.",
]


def run_benchmark(
    backend: LLMBackend,
    prompts: list[str],
    runs: int = 3,
    streaming: bool = False,
) -> dict[str, Any]:
    """Run benchmark on a backend."""
    results = {
        "backend": backend.name,
        "runs": [],
        "summary": {},
    }

    all_stats: list[InferenceStats] = []

    for run_idx in range(runs):
        run_results = []
        for prompt in prompts:
            messages = [ChatMessage(role="user", content=prompt)]

            try:
                if streaming:
                    tokens: list[str] = []

                    def on_token(tok: str) -> None:
                        tokens.append(tok)

                    response = backend.chat_stream(messages, on_token=on_token)
                else:
                    response = backend.chat(messages)

                stats = response.stats
                all_stats.append(stats)
                run_results.append({
                    "prompt": prompt[:50] + "..." if len(prompt) > 50 else prompt,
                    "success": True,
                    "stats": asdict(stats),
                })
                print(f"  [{backend.name}] Run {run_idx+1}: {stats.tokens_per_second:.2f} tok/s, "
                      f"TTFT: {stats.time_to_first_token*1000:.0f}ms" if stats.time_to_first_token else f"  [{backend.name}] Run {run_idx+1}: {stats.tokens_per_second:.2f} tok/s")

            except Exception as e:
                run_results.append({
                    "prompt": prompt[:50] + "..." if len(prompt) > 50 else prompt,
                    "success": False,
                    "error": str(e),
                })
                print(f"  [{backend.name}] Run {run_idx+1}: ERROR - {e}")

        results["runs"].append(run_results)

    # Compute summary statistics
    successful = [s for s in all_stats if s.tokens_per_second and s.tokens_per_second > 0]
    if successful:
        results["summary"] = {
            "avg_tokens_per_second": statistics.mean(s.tokens_per_second for s in successful),
            "median_tokens_per_second": statistics.median(s.tokens_per_second for s in successful),
            "stdev_tokens_per_second": statistics.stdev(s.tokens_per_second for s in successful) if len(successful) > 1 else 0,
            "avg_ttft_ms": statistics.mean(s.time_to_first_token * 1000 for s in successful if s.time_to_first_token) if any(s.time_to_first_token for s in successful) else None,
            "avg_total_time": statistics.mean(s.total_time for s in successful),
            "total_requests": len(successful),
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark LLM backends")
    parser.add_argument("--backend", choices=["llama.cpp", "ipex-llm", "both"], default="llama.cpp")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per prompt")
    parser.add_argument("--streaming", action="store_true", help="Use streaming mode")
    default_llama_url = (
        f"http://{os.getenv('LLAMA_HOST', '127.0.0.1')}:{os.getenv('LLAMA_PORT', '8080')}"
    )
    parser.add_argument("--llama-url", default=default_llama_url, help="llama.cpp server URL")
    parser.add_argument("--model", default="Qwen2.5-7B-Instruct", help="Model name for llama.cpp")
    parser.add_argument("--ipex-model", help="Path to IPEX-LLM model")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()

    backends_to_test = []
    if args.backend in ("llama.cpp", "both"):
        backends_to_test.append(("llama.cpp", {
            "base_url": args.llama_url,
            "model": args.model,
        }))
    if args.backend in ("ipex-llm", "both"):
        if not args.ipex_model:
            print("Error: --ipex-model required for ipex-llm backend")
            return 1
        backends_to_test.append(("ipex-llm", {
            "model_path": args.ipex_model,
        }))

    all_results = {}

    for name, config in backends_to_test:
        print(f"\n{'='*60}")
        print(f"Testing {name} backend...")
        print(f"{'='*60}")

        backend = create_backend(name, **config)

        # Health check
        health = backend.health_check()
        print(f"Health check: {health}")
        if health.get("status") != "healthy":
            print(f"  Backend not healthy, skipping...")
            continue

        try:
            results = run_benchmark(backend, TEST_PROMPTS, runs=args.runs, streaming=args.streaming)
            all_results[name] = results

            # Print summary
            summary = results.get("summary", {})
            if summary:
                print(f"\n  Summary for {name}:")
                print(f"    Avg tokens/sec: {summary.get('avg_tokens_per_second', 0):.2f}")
                print(f"    Median tokens/sec: {summary.get('median_tokens_per_second', 0):.2f}")
                print(f"    Avg TTFT: {summary.get('avg_ttft_ms', 0):.0f}ms")
                print(f"    Avg total time: {summary.get('avg_total_time', 0):.2f}s")
        finally:
            backend.close()

    # Save results
    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults saved to {args.output}")

    # Compare if both backends tested
    if "llama.cpp" in all_results and "ipex-llm" in all_results:
        llama_avg = all_results["llama.cpp"]["summary"].get("avg_tokens_per_second", 0)
        ipex_avg = all_results["ipex-llm"]["summary"].get("avg_tokens_per_second", 0)
        print(f"\n{'='*60}")
        print("COMPARISON:")
        print(f"  llama.cpp: {llama_avg:.2f} tok/s")
        print(f"  ipex-llm:  {ipex_avg:.2f} tok/s")
        if llama_avg > 0:
            diff = ((ipex_avg - llama_avg) / llama_avg) * 100
            print(f"  Difference: {diff:+.1f}%")

    return 0


if __name__ == "__main__":
    exit(main())