from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MERGED = ROOT / "outputs/qinweixi_4b_repair_v1_merged"
LLAMA_CPP = ROOT / "llama.cpp"
F16 = ROOT / "outputs/qinweixi_4b_repair_v1-f16.gguf"
Q4 = ROOT / "outputs/qinweixi_4b_repair_v1-q4_k_m.gguf"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    converter = LLAMA_CPP / "convert_hf_to_gguf.py"
    quantizer = LLAMA_CPP / "build/bin/llama-quantize"
    if sys.platform == "win32":
        quantizer = quantizer.with_suffix(".exe")
    if not converter.is_file() or not quantizer.is_file():
        raise FileNotFoundError("先在包根目录准备并编译 llama.cpp，缺少 converter 或 llama-quantize")
    subprocess.run([sys.executable, str(converter), str(MERGED), "--outfile", str(F16), "--outtype", "f16"], check=True)
    subprocess.run([str(quantizer), str(F16), str(Q4), "Q4_K_M"], check=True)
    manifest = {
        "model_id": "qinweixi-4b-repair-v1-q4_k_m", "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_manifest": json.loads((ROOT / "data_manifest.json").read_text(encoding="utf-8")),
        "adapter_dir": "outputs/qinweixi_4b_repair_v1", "merged_dir": MERGED.relative_to(ROOT).as_posix(),
        "gguf": {"path": Q4.relative_to(ROOT).as_posix(), "bytes": Q4.stat().st_size, "sha256": sha256(Q4)},
        "converter": {"path": converter.relative_to(ROOT).as_posix(), "sha256": sha256(converter)},
        "quantizer": {"path": quantizer.relative_to(ROOT).as_posix(), "sha256": sha256(quantizer)},
    }
    (ROOT / "outputs/export_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest["gguf"], ensure_ascii=False))


if __name__ == "__main__":
    main()
