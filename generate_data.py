"""生成可复现的合成 A/B 实验数据；这些数据不代表任何真实业务。"""

from __future__ import annotations

import argparse
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DAY_SECONDS = 86_400
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "build" / "ab_experiment.sqlite3"
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"


@dataclass(frozen=True)
class ExperimentConfig:
    """集中定义模拟机制；随机种子不得根据结果显著性调整。"""

    n_users: int = 6_000
    seed: int = 20_261_115
    treatment_share: float = 0.5
    control_completion_rate: float = 0.42
    treatment_completion_rate: float = 0.48
    control_d1_rate: float = 0.30
    treatment_d1_rate: float = 0.33
    control_crash_rate: float = 0.020
    treatment_crash_rate: float = 0.021
    android_share: float = 0.60
    duplicate_event_rate: float = 0.02
    experiment_start_date: str = "2026-09-01"
    assignment_days: int = 28


CONFIG = ExperimentConfig()


def _experiment_start_timestamp(config: ExperimentConfig) -> int:
    start = datetime.strptime(config.experiment_start_date, "%Y-%m-%d")
    return int(start.replace(tzinfo=timezone.utc).timestamp())


def generate_rows(
    config: ExperimentConfig = CONFIG,
) -> tuple[list[tuple[str, str, int, str]], list[tuple[str, str, str, int]]]:
    """返回 assignments 和 events；重复事件保留相同 event_id。"""
    rng = random.Random(config.seed)
    experiment_start = _experiment_start_timestamp(config)
    assignments: list[tuple[str, str, int, str]] = []
    events: list[tuple[str, str, str, int]] = []
    boundaries_seen: set[str] = set()

    for index in range(config.n_users):
        user_id = f"u_{index:05d}"
        variant = "treatment" if rng.random() < config.treatment_share else "control"
        assigned_at = experiment_start + rng.randrange(
            config.assignment_days * DAY_SECONDS
        )
        device_type = "android" if rng.random() < config.android_share else "ios"
        assignments.append((user_id, variant, assigned_at, device_type))

        user_events: list[tuple[str, str, str, int]] = []

        def add_event(event_name: str, occurred_at: int) -> None:
            event_id = f"e_{index:05d}_{len(user_events):02d}"
            user_events.append((event_id, user_id, event_name, occurred_at))

        if index == 0:
            # 分流前事件不应进入任何实验指标。
            add_event("tutorial_complete", assigned_at - 1)

        completion_rate = (
            config.treatment_completion_rate
            if variant == "treatment"
            else config.control_completion_rate
        )
        if rng.random() < completion_rate:
            if "completion_start" not in boundaries_seen:
                completion_offset = 0
                boundaries_seen.add("completion_start")
            elif "completion_end" not in boundaries_seen:
                completion_offset = DAY_SECONDS - 1
                boundaries_seen.add("completion_end")
            else:
                completion_offset = rng.randrange(5 * 60, DAY_SECONDS)
            add_event("tutorial_complete", assigned_at + completion_offset)
        elif "completion_outside" not in boundaries_seen:
            # 恰好 24 小时的完成事件不计入主指标。
            add_event("tutorial_complete", assigned_at + DAY_SECONDS)
            boundaries_seen.add("completion_outside")

        d1_rate = (
            config.treatment_d1_rate if variant == "treatment" else config.control_d1_rate
        )
        if rng.random() < d1_rate:
            if "d1_start" not in boundaries_seen:
                app_open_offset = DAY_SECONDS
                boundaries_seen.add("d1_start")
            else:
                app_open_offset = DAY_SECONDS + rng.randrange(DAY_SECONDS)
            add_event("app_open", assigned_at + app_open_offset)
        elif "d1_outside" not in boundaries_seen:
            # D1 窗口结束时的事件不计入 D1 留存。
            add_event("app_open", assigned_at + 2 * DAY_SECONDS)
            boundaries_seen.add("d1_outside")

        crash_rate = (
            config.treatment_crash_rate
            if variant == "treatment"
            else config.control_crash_rate
        )
        if rng.random() < crash_rate:
            add_event("crash", assigned_at + rng.randrange(DAY_SECONDS))

        for event in user_events:
            events.append(event)
            if rng.random() < config.duplicate_event_rate:
                # 模拟日志重传：内容完全相同，摄入顺序不同。
                events.append(event)

    return assignments, events


def insert_rows(
    connection: sqlite3.Connection,
    assignments: list[tuple[str, str, int, str]],
    events: list[tuple[str, str, str, int]],
) -> None:
    connection.executemany(
        "INSERT INTO assignments (user_id, variant, assigned_at, device_type) "
        "VALUES (?, ?, ?, ?)",
        assignments,
    )
    connection.executemany(
        "INSERT INTO events (event_id, user_id, event_name, occurred_at) "
        "VALUES (?, ?, ?, ?)",
        events,
    )


def create_database(
    database_path: Path = DEFAULT_DATABASE_PATH,
    config: ExperimentConfig = CONFIG,
) -> tuple[int, int]:
    """从零重建数据库，返回原始分配记录数和事件摄入行数。"""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        assignments, events = generate_rows(config)
        insert_rows(connection, assignments, events)
        connection.commit()
        return len(assignments), len(events)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    assignment_count, event_count = create_database(args.database)
    print(f"已生成 {assignment_count} 条分流记录、{event_count} 行原始事件。")
    print(f"数据库：{args.database}")


if __name__ == "__main__":
    main()
