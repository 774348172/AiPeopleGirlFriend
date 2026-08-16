"""Run a LLaMA-Factory config while reserving part of the GPU for Windows."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


QUANTIZATION_METHODS = {
    "bnb",
    "gptq",
    "awq",
    "aqlm",
    "quanto",
    "eetq",
    "hqq",
    "mxfp4",
    "fp8",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--gpu-fraction", type=float, default=0.72)
    return parser.parse_args()


def validate_config(path: str) -> None:
    config_path = Path(path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if config.get("quantization_bit") is None:
        return

    method = config.get("quantization_method", "bnb")
    if method not in QUANTIZATION_METHODS:
        allowed = ", ".join(sorted(QUANTIZATION_METHODS))
        raise ValueError(
            f"Invalid quantization_method={method!r} in {config_path}. "
            f"Expected one of: {allowed}. Use 'bnb' for bitsandbytes."
        )


def main() -> None:
    args = parse_args()
    if not 0 < args.gpu_fraction <= 1:
        raise ValueError("--gpu-fraction must be in (0, 1]")
    validate_config(args.config)

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
    os.environ.setdefault("DISABLE_VERSION_CHECK", "1")

    import torch

    if args.gpu_fraction < 1:
        torch.cuda.set_per_process_memory_fraction(args.gpu_fraction)
    print(
        f"[gpu] device={torch.cuda.get_device_name(0)} "
        f"fraction={args.gpu_fraction:.0%}",
        flush=True,
    )

    try:
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000
        )
    except Exception as exc:
        print(f"[gpu] could not lower process priority: {exc}", flush=True)

    from llamafactory.cli import main as llamafactory_main

    sys.argv = ["llamafactory-cli", "train", args.config]
    llamafactory_main()


if __name__ == "__main__":
    main()
