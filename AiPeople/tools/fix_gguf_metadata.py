# -*- coding: utf-8 -*-
"""修复 Qwen3.5-4B GGUF 的 MTP 元数据：block_count 33→32、nextn_predict_layers 1→0。

背景：llama.cpp 转换脚本对 Qwen3.5 的 MTP 层（text_config.mtp_num_hidden_layers=1）
处理有 bug——block_count 计入了 MTP 层但 MTP 权重未转换，导致加载时报
'tensor blk.32.attn_norm.weight not found'。MTP 是训练辅助头，主模型推理不需要，
修正元数据即可正常加载。

用法: python tools/fix_gguf_metadata.py <输入.gguf> <输出.gguf>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from gguf import GGUFReader, GGUFWriter, GGUFValueType


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: python fix_gguf_metadata.py <输入.gguf> <输出.gguf>")
        return 2
    src, dst = sys.argv[1], sys.argv[2]

    reader = GGUFReader(src)
    writer = GGUFWriter(dst, "qwen35")

    # 1) 复制 kv（修正 MTP 相关字段）
    for key, field in reader.fields.items():
        vtype = field.types[0]
        if key == "qwen35.block_count":
            writer.add_uint32(key, 32)
            print("修正 %s: %s -> 32" % (key, field.parts[field.data[0]]))
        elif key == "qwen35.nextn_predict_layers":
            writer.add_uint32(key, 0)
            print("修正 %s: %s -> 0" % (key, field.parts[field.data[0]]))
        elif vtype == GGUFValueType.UINT32:
            writer.add_uint32(key, int(field.parts[field.data[0]]))
        elif vtype == GGUFValueType.INT32:
            writer.add_int32(key, int(field.parts[field.data[0]]))
        elif vtype == GGUFValueType.FLOAT32:
            writer.add_float32(key, float(field.parts[field.data[0]]))
        elif vtype == GGUFValueType.FLOAT64:
            writer.add_float64(key, float(field.parts[field.data[0]]))
        elif vtype == GGUFValueType.BOOL:
            writer.add_bool(key, bool(field.parts[field.data[0]]))
        elif vtype == GGUFValueType.STRING:
            writer.add_string(key, str(field.parts[field.data[0]], encoding="utf-8"))
        elif vtype in (GGUFValueType.ARRAY,):
            arr_type = field.types[1]
            vals = [field.parts[i] for i in field.data]
            if arr_type == GGUFValueType.STRING:
                writer.add_array(key, [str(v, encoding="utf-8") for v in vals])
            elif arr_type == GGUFValueType.UINT32:
                writer.add_array(key, [int(v) for v in vals])
            elif arr_type == GGUFValueType.INT32:
                writer.add_array(key, [int(v) for v in vals])
            elif arr_type == GGUFValueType.FLOAT32:
                writer.add_array(key, [float(v) for v in vals])
            else:
                print("跳过 array kv: %s (type %s)" % (key, arr_type))
        else:
            print("跳过 kv: %s (type %s)" % (key, vtype))

    # 2) 复制张量数据（原样，保持量化 dtype）
    for tensor in reader.tensors:
        writer.add_tensor(tensor.name, np.asarray(tensor.data))

    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    print("修复完成: %s -> %s（%d 个张量）" % (src, dst, len(reader.tensors)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
