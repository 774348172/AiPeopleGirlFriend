"""Freshness at admission, plus durable instance/sequence ordering.

UTC is shared by the local Unity/backend processes. Clock rollback fails closed;
the monotonic lease also prevents rollback from extending a status observation.
This describes observations, not a claim that the socket is still connected.
"""
import time
import uuid

from .unity_snapshot import UnityGameSnapshot

MAX_AGE_MS = 30_000
FUTURE_TOLERANCE_MS = 5_000


class SnapshotRejected(ValueError):
    pass


class UnityFreshness:
    def __init__(self, wall=time.time, monotonic=time.monotonic):
        self.wall = wall
        self.monotonic = monotonic
        self.run_id = uuid.uuid4().hex

    def accept(self, snapshot: UnityGameSnapshot, prior: dict | None) -> dict:
        now = int(self.wall() * 1000)
        if not -FUTURE_TOLERANCE_MS <= now - snapshot.observed_at_ms <= MAX_AGE_MS:
            raise SnapshotRejected("游戏快照已过期或系统时间异常，请重新发送以采集当前状态")
        if prior:
            prior_clock = prior.get('game_clock')
            if prior_clock and snapshot.game_clock.elapsed_seconds < prior_clock['elapsed_seconds']:
                raise SnapshotRejected('游戏时间倒退，请重新同步存档时间')
            if snapshot.instance_id == prior['instance_id']:
                if (snapshot.instance_started_at_ms != prior['instance_started_at_ms']
                        or snapshot.sequence <= prior['sequence']
                        or snapshot.observed_at_ms < prior['observed_at_ms']):
                    raise SnapshotRejected("游戏快照重复或倒退，请重新采集当前状态")
            elif snapshot.instance_started_at_ms <= prior['instance_started_at_ms']:
                raise SnapshotRejected("旧游戏实例已失效，请使用当前游戏窗口重新发送")
        return dict(source=snapshot.source, world_id=snapshot.world_id, schema_version=snapshot.schema_version,
                    instance_id=snapshot.instance_id, sequence=snapshot.sequence,
                    instance_started_at_ms=snapshot.instance_started_at_ms,
                    observed_at_ms=snapshot.observed_at_ms, received_at_ms=now,
                    received_monotonic=self.monotonic(), backend_run_id=self.run_id,
                    game_clock=snapshot.game_clock.model_dump())

    def status(self, record: dict | None) -> dict:
        if record is None:
            return dict(status='unknown', source='unverified', reason='尚未收到带采集时间的游戏快照')
        age = int(self.wall() * 1000) - record['observed_at_ms']
        reason = ''
        if record['backend_run_id'] != self.run_id:
            reason = '后端已重启，等待游戏重新同步'
        elif (not -FUTURE_TOLERANCE_MS <= age <= MAX_AGE_MS
              or self.monotonic() - record['received_monotonic'] > MAX_AGE_MS / 1000):
            reason = '未收到新快照，当前状态待重新同步'
        return dict(status='stale' if reason else 'current', source=record['source'],
                    reason=reason, observed_at_ms=record['observed_at_ms'],
                    received_at_ms=record['received_at_ms'], age_ms=max(0, age),
                    max_age_ms=MAX_AGE_MS, instance_id=record['instance_id'],
                    sequence=record['sequence'])
