from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LLAMA_CPP = Path("D:/AiPeople/tools/llamacpp")
QUANTIZER = Path("D:/AiPeople/tools/llamacpp-b10455/llama-quantize.exe")
CONFIGS = {
    "qwen35_9b": {
        "merged": ROOT / "outputs" / "baiweixi_qwen35_9b_merged_text_v2",
        "f16": ROOT / "outputs" / "baiweixi_qwen35_9b_text_v2_f16.gguf",
        "quantized": ROOT / "outputs" / "baiweixi_qwen35_9b_q6_k.gguf",
        "quantization": "Q6_K",
        "converter_args": ["--no-mtp"],
    },
    "qwen3_14b": {
        "merged": ROOT / "outputs" / "baiweixi_qwen3_14b_merged_text_v2",
        "f16": ROOT / "outputs" / "baiweixi_qwen3_14b_text_v2_f16.gguf",
        "quantized": ROOT / "outputs" / "baiweixi_qwen3_14b_q4_k_m.gguf",
        "quantization": "Q4_K_M",
        "converter_args": [],
    },
    "qwen3_14b_thinking_preserve_v2": {
        "merged": ROOT / "outputs" / "baiweixi_qwen3_14b_thinking_preserve_v2_merged",
        "f16": ROOT / "outputs" / "baiweixi_qwen3_14b_thinking_preserve_v2_f16.gguf",
        "quantized": ROOT / "outputs" / "baiweixi_qwen3_14b_thinking_preserve_v2_q4_k_m.gguf",
        "quantization": "Q4_K_M",
        "converter_args": [],
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_converter_python() -> str:
    candidates = [sys.executable, shutil.which("python")]
    checked: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        executable = str(Path(candidate).resolve())
        if executable in checked:
            continue
        checked.add(executable)
        result = subprocess.run(
            [executable, "-c", "import gguf"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return executable
    raise RuntimeError(
        "No Python interpreter with the llama.cpp gguf package was found"
    )


def run(command: list[str], log_path: Path) -> float:
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        exit_code = process.wait()
    if exit_code:
        raise subprocess.CalledProcessError(exit_code, command)
    return time.time() - started


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Merge and quantize a trained Baiweixi LoRA candidate")
    parser.add_argument("model_key", choices=sorted(CONFIGS))
    args = parser.parse_args()
    config = CONFIGS[args.model_key]
    converter = LLAMA_CPP / "convert_hf_to_gguf.py"
    for path in (config["merged"], converter, QUANTIZER):
        if Path(path).is_dir():
            continue
        if not Path(path).is_file():
            raise FileNotFoundError(path)

    metrics: dict[str, object] = {"model_key": args.model_key}
    converter_python = find_converter_python()
    metrics["converter_python"] = converter_python
    metrics["convert_seconds"] = run(
        [
            converter_python,
            str(converter),
            str(config["merged"]),
            "--outfile",
            str(config["f16"]),
            "--outtype",
            "f16",
            *config["converter_args"],
        ],
        ROOT / f"convert_{args.model_key}.log",
    )
    metrics["quantize_seconds"] = run(
        [
            str(QUANTIZER),
            str(config["f16"]),
            str(config["quantized"]),
            str(config["quantization"]),
        ],
        ROOT / f"quantize_{args.model_key}.log",
    )
    quantized = Path(config["quantized"])
    metrics.update(
        {
            "quantization": config["quantization"],
            "f16_path": str(Path(config["f16"]).resolve()),
            "f16_bytes": Path(config["f16"]).stat().st_size,
            "quantized_path": str(quantized.resolve()),
            "quantized_bytes": quantized.stat().st_size,
            "quantized_sha256": sha256(quantized),
        }
    )
    metrics_path = ROOT / "outputs" / f"{args.model_key}_export_metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
