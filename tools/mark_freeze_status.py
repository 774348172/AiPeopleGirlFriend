"""阶段 0 冻结：给 metadata 打 release/review 状态标记（v2 施工总计划 §三 阶段 0）。

用法:
    python tools/mark_freeze_status.py <metadata.jsonl> <status>

status 取值（v2 术语）:
    candidate_unreviewed    — 997 条：生成通过但未经 v2 标准验收，不得视为 release
    human_review_incomplete — 119 条：复核表未勾选，人工复核未完成

语义:
    - 幂等：行内已有相同 review_status 则跳过；已有不同值则报错退出（防止覆盖已晋级状态）
    - 原地改写，保持行序；每行新增字段 "review_status": <status>
"""
import json
import sys
from pathlib import Path

KNOWN_STATUSES = {"candidate_unreviewed", "human_review_incomplete"}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    status = sys.argv[2]
    if status not in KNOWN_STATUSES:
        print(f"未知状态: {status}（可选: {sorted(KNOWN_STATUSES)}）")
        return 2
    if not path.exists():
        print(f"文件不存在: {path}")
        return 2

    changed = 0
    skipped = 0
    conflict = 0
    lines_out = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                lines_out.append("")
                continue
            rec = json.loads(line)
            old = rec.get("review_status")
            if old is None:
                rec["review_status"] = status
                changed += 1
            elif old == status:
                skipped += 1
            else:
                conflict += 1
                print(f"冲突: 行 {lineno} 已有 review_status={old!r}，拒绝覆盖")
            lines_out.append(json.dumps(rec, ensure_ascii=False))

    if conflict:
        print(f"中止：{conflict} 行状态冲突，未写回")
        return 1

    path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    print(f"OK: {path}  total={changed + skipped + conflict}  marked={changed}  already={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
