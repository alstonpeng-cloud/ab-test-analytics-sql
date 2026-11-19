from __future__ import annotations

import math
import sqlite3
import sys
import unittest
from pathlib import Path
from statistics import NormalDist

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from generate_data import CONFIG, DAY_SECONDS, generate_rows, insert_rows
from run_analysis import SQL_DIR, Z_975, configure_connection, execute_analysis


class AnalysisTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = configure_connection(sqlite3.connect(":memory:"))
        self.connection.executescript(
            (PROJECT_ROOT / "schema.sql").read_text(encoding="utf-8")
        )
        self._create_analysis_views()

    def tearDown(self) -> None:
        self.connection.close()

    def _create_analysis_views(self) -> None:
        self.connection.executescript(
            (SQL_DIR / "01_analysis_base.sql").read_text(encoding="utf-8")
        )

    def _insert(
        self,
        assignments: list[tuple[str, str, int, str]],
        events: list[tuple[str, str, str, int]] | None = None,
    ) -> None:
        insert_rows(self.connection, assignments, events or [])

    def _query_one(self, filename: str) -> dict:
        row = self.connection.execute(
            (SQL_DIR / filename).read_text(encoding="utf-8")
        ).fetchone()
        return dict(row)


class AnalysisBaseTests(AnalysisTestCase):
    def test_itt_keeps_no_event_users_and_removes_duplicate_events(self) -> None:
        assigned_at = 1_000_000
        self._insert(
            [
                ("u1", "control", assigned_at, "android"),
                ("u2", "treatment", assigned_at, "ios"),
            ],
            [
                ("event-1", "u1", "tutorial_complete", assigned_at + 60),
                ("event-1", "u1", "tutorial_complete", assigned_at + 60),
                ("event-2", "u1", "crash", assigned_at + 30),
                ("event-2", "u1", "crash", assigned_at + 30),
            ],
        )

        rows = self.connection.execute(
            "SELECT * FROM v_analysis_base ORDER BY user_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["completed_24h"], rows[0]["crashed_24h"]), (1, 1))
        self.assertEqual(
            (rows[1]["completed_24h"], rows[1]["retained_d1"]), (0, 0)
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM v_events_deduped"
            ).fetchone()[0],
            2,
        )
        primary = self._query_one("04_primary_metric.sql")
        self.assertEqual(primary["control_n"], 1)
        self.assertEqual(primary["treatment_n"], 1)

    def test_time_windows_are_half_open(self) -> None:
        assigned_at = 1_000_000
        users = [
            ("u_start", "control", assigned_at, "android"),
            ("u_last", "control", assigned_at, "android"),
            ("u_out", "treatment", assigned_at, "ios"),
            ("u_pre", "treatment", assigned_at, "ios"),
        ]
        events = [
            ("e1", "u_start", "tutorial_complete", assigned_at),
            ("e2", "u_start", "app_open", assigned_at + DAY_SECONDS),
            ("e3", "u_start", "crash", assigned_at),
            ("e4", "u_last", "tutorial_complete", assigned_at + DAY_SECONDS - 1),
            ("e5", "u_last", "app_open", assigned_at + 2 * DAY_SECONDS - 1),
            ("e6", "u_last", "crash", assigned_at + DAY_SECONDS - 1),
            ("e7", "u_out", "tutorial_complete", assigned_at + DAY_SECONDS),
            ("e8", "u_out", "app_open", assigned_at + 2 * DAY_SECONDS),
            ("e9", "u_out", "crash", assigned_at + DAY_SECONDS),
            ("e10", "u_pre", "tutorial_complete", assigned_at - 1),
            ("e11", "u_pre", "app_open", assigned_at + DAY_SECONDS - 1),
            ("e12", "u_pre", "crash", assigned_at - 1),
        ]
        self._insert(users, events)

        rows = self.connection.execute(
            "SELECT user_id, completed_24h, retained_d1, crashed_24h "
            "FROM v_analysis_base ORDER BY user_id"
        ).fetchall()
        observed = {
            row["user_id"]: (
                row["completed_24h"],
                row["retained_d1"],
                row["crashed_24h"],
            )
            for row in rows
        }
        self.assertEqual(observed["u_start"], (1, 1, 1))
        self.assertEqual(observed["u_last"], (1, 1, 1))
        self.assertEqual(observed["u_out"], (0, 0, 0))
        self.assertEqual(observed["u_pre"], (0, 0, 0))

    def test_conflicting_event_id_stops_analysis(self) -> None:
        assigned_at = 1_000_000
        self._insert(
            [
                ("u1", "control", assigned_at, "android"),
                ("u2", "treatment", assigned_at, "ios"),
            ],
            [
                ("same-id", "u1", "crash", assigned_at + 1),
                ("same-id", "u2", "crash", assigned_at + 1),
            ],
        )
        with self.assertRaisesRegex(RuntimeError, "conflicting_event_ids"):
            execute_analysis(self.connection)

    def test_srm_failure_stops_analysis(self) -> None:
        assignments = [
            (f"u{index}", "control", 1_000_000 + index, "android")
            for index in range(100)
        ]
        self._insert(assignments)
        with self.assertRaisesRegex(RuntimeError, "SRM"):
            execute_analysis(self.connection)

    def test_schema_rejects_invalid_variant_and_orphan_event(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert([("u1", "invalid", 1_000_000, "android")])
        self.connection.rollback()
        self._insert([("u1", "control", 1_000_000, "android")])
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert([], [("e1", "missing", "crash", 1_000_001)])


class StatisticalValidationTests(AnalysisTestCase):
    def _insert_binary_fixture(
        self,
        control_n: int,
        control_conversions: int,
        treatment_n: int,
        treatment_conversions: int,
    ) -> None:
        assignments = []
        events = []
        for variant, n, conversions in (
            ("control", control_n, control_conversions),
            ("treatment", treatment_n, treatment_conversions),
        ):
            for index in range(n):
                user_id = f"{variant}-{index}"
                assigned_at = 1_000_000 + index
                assignments.append((user_id, variant, assigned_at, "android"))
                if index < conversions:
                    events.append(
                        (
                            f"event-{variant}-{index}",
                            user_id,
                            "tutorial_complete",
                            assigned_at + 60,
                        )
                    )
        self._insert(assignments, events)

    def test_primary_metric_matches_independent_statistical_formulas(self) -> None:
        self._insert_binary_fixture(4, 1, 4, 3)
        result = self._query_one("04_primary_metric.sql")

        control_rate = 1 / 4
        treatment_rate = 3 / 4
        difference = treatment_rate - control_rate
        unpooled_se = math.sqrt(
            control_rate * (1 - control_rate) / 4
            + treatment_rate * (1 - treatment_rate) / 4
        )
        pooled_rate = 4 / 8
        pooled_se = math.sqrt(pooled_rate * (1 - pooled_rate) * (1 / 4 + 1 / 4))
        z_score = difference / pooled_se
        expected_p = 2 * (1 - NormalDist().cdf(abs(z_score)))

        self.assertEqual(result["control_conversions"], 1)
        self.assertEqual(result["treatment_conversions"], 3)
        self.assertAlmostEqual(result["control_rate"], control_rate)
        self.assertAlmostEqual(result["treatment_rate"], treatment_rate)
        self.assertAlmostEqual(result["absolute_lift"], difference)
        self.assertAlmostEqual(result["relative_lift"], 2.0)
        self.assertAlmostEqual(result["ci_lower"], difference - Z_975 * unpooled_se)
        self.assertAlmostEqual(result["ci_upper"], difference + Z_975 * unpooled_se)
        self.assertAlmostEqual(result["z_score"], z_score)
        self.assertAlmostEqual(result["p_value"], expected_p)

    def test_zero_standard_error_and_zero_control_rate_are_explicit(self) -> None:
        self._insert_binary_fixture(4, 4, 4, 4)
        all_converted = self._query_one("04_primary_metric.sql")
        self.assertIsNone(all_converted["z_score"])
        self.assertEqual(all_converted["p_value"], 1.0)
        self.assertEqual(all_converted["absolute_lift"], 0)

        self.connection.execute("DELETE FROM events")
        self.connection.execute("DELETE FROM assignments")
        self._insert_binary_fixture(4, 0, 4, 2)
        zero_control = self._query_one("04_primary_metric.sql")
        self.assertEqual(zero_control["control_rate"], 0)
        self.assertIsNone(zero_control["relative_lift"])


class FullDatasetValidationTests(AnalysisTestCase):
    def setUp(self) -> None:
        super().setUp()
        assignments, events = generate_rows(CONFIG)
        self._insert(assignments, events)
        self.results = execute_analysis(self.connection)

    def test_fixed_seed_regenerates_identical_raw_rows(self) -> None:
        self.assertEqual(generate_rows(CONFIG), generate_rows(CONFIG))

    def test_full_dataset_matches_independent_python_recalculation(self) -> None:
        assignment_rows = self.connection.execute(
            "SELECT user_id, variant, assigned_at, device_type FROM assignments"
        ).fetchall()
        raw_events = self.connection.execute(
            "SELECT ingest_id, event_id, user_id, event_name, occurred_at "
            "FROM events ORDER BY ingest_id"
        ).fetchall()

        seen_event_ids = set()
        deduplicated_events = []
        for event in raw_events:
            if event["event_id"] not in seen_event_ids:
                seen_event_ids.add(event["event_id"])
                deduplicated_events.append(event)

        users = {
            row["user_id"]: {
                "variant": row["variant"],
                "device_type": row["device_type"],
                "assigned_at": row["assigned_at"],
                "completed_24h": 0,
                "retained_d1": 0,
                "crashed_24h": 0,
            }
            for row in assignment_rows
        }
        for event in deduplicated_events:
            user = users[event["user_id"]]
            offset = event["occurred_at"] - user["assigned_at"]
            if event["event_name"] == "tutorial_complete" and 0 <= offset < DAY_SECONDS:
                user["completed_24h"] = 1
            if event["event_name"] == "app_open" and DAY_SECONDS <= offset < 2 * DAY_SECONDS:
                user["retained_d1"] = 1
            if event["event_name"] == "crash" and 0 <= offset < DAY_SECONDS:
                user["crashed_24h"] = 1

        aggregates = {}
        for variant in ("control", "treatment"):
            variant_users = [u for u in users.values() if u["variant"] == variant]
            aggregates[variant] = {
                "n": len(variant_users),
                **{
                    metric: sum(user[metric] for user in variant_users)
                    for metric in ("completed_24h", "retained_d1", "crashed_24h")
                },
            }

        control = aggregates["control"]
        treatment = aggregates["treatment"]
        primary = self.results["primary"]
        self.assertEqual(primary["control_n"], control["n"])
        self.assertEqual(primary["treatment_n"], treatment["n"])
        self.assertEqual(primary["control_conversions"], control["completed_24h"])
        self.assertEqual(primary["treatment_conversions"], treatment["completed_24h"])

        control_rate = control["completed_24h"] / control["n"]
        treatment_rate = treatment["completed_24h"] / treatment["n"]
        difference = treatment_rate - control_rate
        unpooled_se = math.sqrt(
            control_rate * (1 - control_rate) / control["n"]
            + treatment_rate * (1 - treatment_rate) / treatment["n"]
        )
        pooled_rate = (control["completed_24h"] + treatment["completed_24h"]) / (
            control["n"] + treatment["n"]
        )
        pooled_se = math.sqrt(
            pooled_rate
            * (1 - pooled_rate)
            * (1 / control["n"] + 1 / treatment["n"])
        )
        z_score = difference / pooled_se
        expected_p = 2 * (1 - NormalDist().cdf(abs(z_score)))
        self.assertAlmostEqual(primary["absolute_lift"], difference, places=12)
        self.assertAlmostEqual(
            primary["ci_lower"], difference - Z_975 * unpooled_se, places=12
        )
        self.assertAlmostEqual(
            primary["ci_upper"], difference + Z_975 * unpooled_se, places=12
        )
        self.assertAlmostEqual(primary["z_score"], z_score, places=12)
        self.assertAlmostEqual(primary["p_value"], expected_p, places=12)

        secondary = {
            row["metric_name"]: row
            for row in self.results["secondary_guardrail"]
        }
        self.assertEqual(
            secondary["d1_retention"]["control_conversions"], control["retained_d1"]
        )
        self.assertEqual(
            secondary["d1_retention"]["treatment_conversions"],
            treatment["retained_d1"],
        )
        self.assertEqual(
            secondary["crash_24h"]["control_conversions"], control["crashed_24h"]
        )
        self.assertEqual(
            secondary["crash_24h"]["treatment_conversions"], treatment["crashed_24h"]
        )
        self.assertIsNone(secondary["crash_24h"]["p_value"])

        expected_n = len(users) / 2
        chi_square = sum(
            (aggregates[variant]["n"] - expected_n) ** 2 / expected_n
            for variant in ("control", "treatment")
        )
        expected_srm_p = 2 * (1 - NormalDist().cdf(math.sqrt(chi_square)))
        self.assertAlmostEqual(self.results["srm"]["chi_square"], chi_square)
        self.assertAlmostEqual(self.results["srm"]["p_value"], expected_srm_p)

        duplicate_check = next(
            row
            for row in self.results["quality"]
            if row["check_name"] == "duplicate_ingest_rows_removed"
        )
        self.assertEqual(
            duplicate_check["failed_rows"], len(raw_events) - len(deduplicated_events)
        )
        self.assertGreater(duplicate_check["failed_rows"], 0)


if __name__ == "__main__":
    unittest.main()
