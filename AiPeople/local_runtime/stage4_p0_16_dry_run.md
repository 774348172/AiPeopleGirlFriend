# 阶段 4 P0-16 性能与稳定性报告

> 开始：2026-08-04T19:32:03.423172+08:00  
> 完成：2026-08-04T19:32:18.593223+08:00  
> 模型 SHA-256：`9dc8142007be1cd776e360d89f09ea8cd4533bf8f251d95ec2a0cce4dcff4eb6`

## 性能

| 目标 token | 有效样本 | 首字 P95 ms | 完成 P95 ms | 生成速度 P50 token/s |
|---:|---:|---:|---:|---:|
| 80 | 1 | 36.282 | 832.146 | 100.637 |
| 160 | 1 | 38.746 | 1640.721 | 99.940 |

- 冷启动及预热：4.602 秒
- 峰值模型进程显存：N/A MiB
- 峰值整卡显存：4376.000 MiB
- 相对启动前峰值显存增量：2949.000 MiB
- 峰值工作集：2826.395 MiB
- 峰值句柄：211
- 稳定性时长：1.672 秒

## 验收

- [ ] `sample_count`
- [x] `first_delta_p95_under_2s`
- [x] `80_tokens_p95_under_5s`
- [x] `160_tokens_p95_under_8s`
- [x] `peak_gpu_delta_under_3_5gib`
- [ ] `one_hour_completed`
- [x] `no_sustained_resource_growth`

原始逐轮样本保存在同名 JSON 文件中。报告不保存 prompt 或模型回复正文。
