"""一条命令重建合成数据、执行 SQL 并生成可复现的分析结果。"""

from __future__ import annotations

import csv
import math
import sqlite3
from pathlib import Path

from generate_data import DEFAULT_DATABASE_PATH, create_database

PROJECT_ROOT = Path(__file__).resolve().parent
SQL_DIR = PROJECT_ROOT / "sql"
RESULTS_DIR = PROJECT_ROOT / "results"
README_PATH = PROJECT_ROOT / "README.md"
RESULTS_START = "<!-- AUTO_RESULTS:START -->"
RESULTS_END = "<!-- AUTO_RESULTS:END -->"
Z_975 = 1.959963984540054

METRIC_LABELS = {
    "activation_24h": "24 小时教程完成率",
    "d1_retention": "D1 留存率",
    "crash_24h": "24 小时崩溃率",
}
ROLE_LABELS = {
    "primary": "主指标（确认性）",
    "secondary": "次要指标（探索性）",
    "guardrail": "护栏指标",
    "exploratory": "设备分层（探索性）",
}


def configure_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    """配置每条约统计查询所需的确定性数学函数和外键。"""
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.create_function("sqrt", 1, math.sqrt, deterministic=True)
    connection.create_function("erfc", 1, math.erfc, deterministic=True)
    return connection


def connect_database(database_path: Path = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    return configure_connection(sqlite3.connect(database_path))


def _query_rows(connection: sqlite3.Connection, filename: str) -> list[dict]:
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    return [dict(row) for row in connection.execute(sql).fetchall()]


def execute_analysis(connection: sqlite3.Connection) -> dict:
    """先验证数据与分流，再计算指标；质量失败时不返回实验结论。"""
    connection.executescript(
        (SQL_DIR / "01_analysis_base.sql").read_text(encoding="utf-8")
    )
    quality = _query_rows(connection, "02_data_quality.sql")
    failures = [
        row
        for row in quality
        if row["severity"] == "error" and row["failed_rows"] != 0
    ]
    if failures:
        details = ", ".join(
            f"{row['check_name']}={row['failed_rows']}" for row in failures
        )
        raise RuntimeError(f"数据质量检查失败：{details}")

    srm = _query_rows(connection, "03_srm.sql")[0]
    if srm["status"] != "PASS":
        raise RuntimeError(
            f"SRM 检查失败：status={srm['status']}, p_value={srm['p_value']}"
        )

    return {
        "quality": quality,
        "srm": srm,
        "primary": _query_rows(connection, "04_primary_metric.sql")[0],
        "secondary_guardrail": _query_rows(
            connection, "05_secondary_guardrail.sql"
        ),
        "device_subgroup": _query_rows(connection, "06_device_subgroup.sql"),
    }


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _pp(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f} pp"


def _p_value(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


def _ci(row: dict) -> str:
    return f"[{_pp(row['ci_lower'])}, {_pp(row['ci_upper'])}]"


def _primary_table(primary: dict) -> str:
    return "\n".join(
        [
            "| 对照组 | 实验组 | 绝对提升 | 相对提升 | 绝对提升 95% CI | 双侧 p 值 |",
            "|---:|---:|---:|---:|---:|---:|",
            "| {control} | {treatment} | {lift} | {relative} | {ci} | {p} |".format(
                control=f"{_percent(primary['control_rate'])} (n={primary['control_n']})",
                treatment=f"{_percent(primary['treatment_rate'])} (n={primary['treatment_n']})",
                lift=_pp(primary["absolute_lift"]),
                relative=_percent(primary["relative_lift"]),
                ci=_ci(primary),
                p=_p_value(primary["p_value"]),
            ),
        ]
    )


def _render_conclusions(results: dict) -> str:
    primary = results["primary"]
    if (
        primary["p_value"] is not None
        and primary["p_value"] < 0.05
        and primary["ci_lower"] > 0
    ):
        primary_conclusion = (
            f"本次合成数据中，实验组主指标更高：绝对提升 {_pp(primary['absolute_lift'])}，"
            f"95% CI 为 {_ci(primary)}，双侧 p={_p_value(primary['p_value'])}。"
            "可以拒绝‘两组完成率相同’的原假设，但结论只适用于本模拟实验。"
        )
    else:
        primary_conclusion = (
            "本次合成数据未提供主指标正向且统计显著的证据；应报告估计值与不确定性，"
            "而不是宣称实验有效。"
        )

    d1 = next(
        row
        for row in results["secondary_guardrail"]
        if row["metric_name"] == "d1_retention"
    )
    crash = next(
        row
        for row in results["secondary_guardrail"]
        if row["metric_name"] == "crash_24h"
    )
    return "\n".join(
        [
            f"1. **主指标：**{primary_conclusion}",
            f"2. **次要指标：**D1 留存率差为 {_pp(d1['absolute_lift'])}，"
            f"名义 p={_p_value(d1['p_value'])}。未做多重比较校正，只作为探索性证据。",
            f"3. **护栏指标：**崩溃率差为 {_pp(crash['absolute_lift'])}，"
            f"95% CI 为 {_ci(crash)}。区间同时包含风险上升和下降，且未预设非劣效界值，"
            "不能据此证明‘没有安全风险’。",
            "4. **设备分层：**分层结果只用于观察效应估计是否一致，不用于挑选‘最优人群’，"
            "也不构成交互作用的确认性检验。",
        ]
    )


def render_summary(results: dict, assignment_count: int, event_count: int) -> str:
    primary = results["primary"]
    srm = results["srm"]
    lines = [
        "# 实验分析结果",
        "",
        "> 本文件由 `python run_analysis.py` 自动生成。所有数据均为固定随机种子的合成数据，",
        "> 不代表真实用户、真实游戏或真实业务结论。",
        "",
        "## 输入与质量检查",
        "",
        f"- 分流用户：{assignment_count}；原始事件摄入行：{event_count}。",
        f"- SRM：对照 {srm['control_n']}、实验 {srm['treatment_n']}，"
        f"chi-square={srm['chi_square']:.6f}，p={_p_value(srm['p_value'])}，"
        f"状态 **{srm['status']}**。",
        "",
        "| 检查项 | 行数 | 级别 |",
        "|---|---:|---|",
    ]
    lines.extend(
        f"| `{row['check_name']}` | {row['failed_rows']} | {row['severity']} |"
        for row in results["quality"]
    )
    lines.extend(
        [
            "",
            "## 主指标：24 小时教程完成率",
            "",
            _primary_table(primary),
            "",
            "## 次要指标与护栏指标",
            "",
            "| 指标 | 角色 | 对照组 | 实验组 | 差值 | 差值 95% CI | 名义 p 值 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in results["secondary_guardrail"]:
        lines.append(
            "| {metric} | {role} | {control} | {treatment} | {lift} | {ci} | {p} |".format(
                metric=METRIC_LABELS[row["metric_name"]],
                role=ROLE_LABELS[row["metric_role"]],
                control=_percent(row["control_rate"]),
                treatment=_percent(row["treatment_rate"]),
                lift=_pp(row["absolute_lift"]),
                ci=_ci(row),
                p=_p_value(row["p_value"]),
            )
        )
    lines.extend(
        [
            "",
            "## 设备分层（探索性）",
            "",
            "| 设备 | 对照组完成率 | 实验组完成率 | 绝对提升 | 绝对提升 95% CI |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in results["device_subgroup"]:
        lines.append(
            f"| {row['device_type']} | {_percent(row['control_rate'])} "
            f"| {_percent(row['treatment_rate'])} | {_pp(row['absolute_lift'])} "
            f"| {_ci(row)} |"
        )
    lines.extend(["", "## 结果解读", "", _render_conclusions(results), ""])
    return "\n".join(lines)


def render_readme_results(results: dict) -> str:
    return "\n".join(
        [
            "> 以下数值由 `run_analysis.py` 通过 SQL 实际计算并自动更新；仅适用于合成数据。",
            "",
            _primary_table(results["primary"]),
            "",
            f"SRM 检查：**{results['srm']['status']}**（p={_p_value(results['srm']['p_value'])}）。",
            "完整质量检查、次要指标、护栏指标、设备分层和解读见 "
            "[results/summary.md](results/summary.md)。",
        ]
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(content)


def _update_readme(results: dict) -> None:
    content = README_PATH.read_text(encoding="utf-8")
    if RESULTS_START not in content or RESULTS_END not in content:
        raise RuntimeError("README 缺少自动结果标记。")
    before, remainder = content.split(RESULTS_START, 1)
    _, after = remainder.split(RESULTS_END, 1)
    updated = (
        before
        + RESULTS_START
        + "\n"
        + render_readme_results(results)
        + "\n"
        + RESULTS_END
        + after
    )
    _write_text(README_PATH, updated)


def _write_metrics_csv(results: dict) -> None:
    fieldnames = [
        "metric_name",
        "analysis_role",
        "segment",
        "control_n",
        "treatment_n",
        "control_conversions",
        "treatment_conversions",
        "control_rate",
        "treatment_rate",
        "absolute_lift",
        "relative_lift",
        "ci_lower",
        "ci_upper",
        "z_score",
        "p_value",
    ]
    rows = []
    primary = {
        key: results["primary"].get(key)
        for key in fieldnames
        if key in results["primary"]
    }
    primary.update(
        metric_name="activation_24h", analysis_role="primary", segment="overall"
    )
    rows.append(primary)

    for source in results["secondary_guardrail"]:
        row = {key: source.get(key) for key in fieldnames if key in source}
        row.update(analysis_role=source["metric_role"], segment="overall")
        rows.append(row)

    for source in results["device_subgroup"]:
        row = {key: source.get(key) for key in fieldnames if key in source}
        row.update(
            metric_name="activation_24h",
            analysis_role=source["analysis_role"],
            segment=source["device_type"],
            z_score=None,
            p_value=None,
        )
        rows.append(row)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / "metrics.csv").open(
        "w", encoding="utf-8", newline=""
    ) as file:
        writer = csv.DictWriter(
            file, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def run() -> dict:
    assignment_count, event_count = create_database(DEFAULT_DATABASE_PATH)
    connection = connect_database(DEFAULT_DATABASE_PATH)
    try:
        results = execute_analysis(connection)
    finally:
        connection.close()

    _write_text(
        RESULTS_DIR / "summary.md",
        render_summary(results, assignment_count, event_count),
    )
    _write_metrics_csv(results)
    _update_readme(results)
    return results


def main() -> None:
    results = run()
    primary = results["primary"]
    print("分析与结果文件生成完成。")
    print(f"SRM：{results['srm']['status']} (p={_p_value(results['srm']['p_value'])})")
    print(
        "24 小时教程完成率："
        f"{_percent(primary['control_rate'])} -> {_percent(primary['treatment_rate'])}，"
        f"绝对提升 {_pp(primary['absolute_lift'])}，p={_p_value(primary['p_value'])}。"
    )
    print(f"结果目录：{RESULTS_DIR}")


if __name__ == "__main__":
    main()
