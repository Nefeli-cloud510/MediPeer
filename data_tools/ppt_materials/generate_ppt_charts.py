"""Generate PPT material charts and the P8 distribution CSV.

This script is read-only with respect to the case index and writes only to
`outputs/ppt_materials/`. It can be rerun as the case index changes.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
CASE_SCOPE = ROOT / "data_app_chain" / "runs" / "t5_20261105T152844Z" / "case_scope.csv"
OUT_DIR = ROOT / "outputs" / "ppt_materials"


def setup_chinese_font() -> None:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            font_manager.fontManager.addfont(str(path))
            prop = font_manager.FontProperties(fname=str(path))
            family = prop.get_name()
            plt.rcParams["font.family"] = family
            plt.rcParams["axes.unicode_minus"] = False
            return
    plt.rcParams["axes.unicode_minus"] = False


def bucket_counts(values: pd.Series) -> pd.DataFrame:
    counts = {
        "1条": int((values == 1).sum()),
        "2–4条": int(((values >= 2) & (values <= 4)).sum()),
        "5–9条": int(((values >= 5) & (values <= 9)).sum()),
        "10–19条": int(((values >= 10) & (values <= 19)).sum()),
        "20条及以上": int((values >= 20).sum()),
    }
    total = sum(counts.values())
    if total != len(values):
        raise ValueError(f"bucket total {total} != rows {len(values)}")
    df = pd.DataFrame(
        {
            "bucket": counts.keys(),
            "count": counts.values(),
            "share": [v / total for v in counts.values()],
        }
    )
    return df


def bar_chart(
    df: pd.DataFrame,
    total: int,
    title: str,
    subtitle: str,
    filename: str,
    median: int,
    min_two_share: float,
) -> None:
    fig, ax = plt.subplots(figsize=(9.2, 5.2), dpi=160)
    bars = ax.barh(
        df["bucket"],
        df["count"],
        color=["#0ea5a5", "#17a2b8", "#14b8a6", "#38bdf8", "#0891b2"],
        edgecolor="#083344",
        linewidth=0.6,
    )
    for bar, share in zip(bars, df["share"], strict=False):
        ax.text(
            bar.get_width() + total * 0.008,
            bar.get_y() + bar.get_height() / 2,
            f"{int(bar.get_width()):,}  ({share:.1%})",
            va="center",
            fontsize=10,
            color="#134e4a",
        )
    ax.set_title(f"{title}\n{subtitle}", fontsize=13, pad=14, loc="left")
    ax.set_xlabel("问题数（按回答条数统计）", fontsize=10)
    ax.set_xlim(0, total * 0.58)
    ax.tick_params(axis="y", labelsize=11)
    ax.grid(axis="x", linestyle="--", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    note = f"n={total:,} 个独立问题，每题只计一次；中位数 {median} 条；至少 2 条回答占 {min_two_share:.1%}"
    fig.text(0.02, 0.015, note, fontsize=9, color="#475569")
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    fig.savefig(OUT_DIR / filename, bbox_inches="tight")
    plt.close(fig)


def structure_diagram() -> None:
    fig, ax = plt.subplots(figsize=(9.2, 4.6), dpi=160)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    def box(x, y, w, h, text, facecolor="#e0f2f1", edge="#0f766e"):
        ax.add_patch(
            plt.Rectangle(
                (x, y), w, h, facecolor=facecolor, edgecolor=edge, linewidth=1.2
            )
        )
        ax.text(
            x + w / 2,
            y + h / 2,
            text,
            ha="center",
            va="center",
            fontsize=10.5,
            color="#134e4a",
            wrap=True,
        )

    box(0.3, 2.2, 2.2, 1.6, "独立咨询问题\n7,000 个", facecolor="#ccfbf1")
    box(3.3, 3.4, 6.2, 1.5, "患者描述 + 多条医生回答", facecolor="#dbecff")
    box(3.3, 1.2, 1.9, 1.2, "医生回答 1", facecolor="#f0fdfa")
    box(5.5, 1.2, 1.9, 1.2, "医生回答 2", facecolor="#f0fdfa")
    box(7.7, 1.2, 1.9, 1.2, "医生回答 n", facecolor="#f0fdfa")
    for x in (4.0, 6.2, 8.4):
        ax.annotate(
            "",
            xy=(x + 0.55, 2.9),
            xytext=(x, 2.4),
            arrowprops=dict(arrowstyle="-|>", color="#64748b"),
        )
    ax.text(0.3, 4.4, "同一问题聚合", fontsize=11, color="#0f766e", fontweight="bold")
    ax.text(
        3.3,
        5.3,
        "时间范围：2017-09-02 至 2021-10-07（本地样本已核实口径）",
        fontsize=10,
        color="#134e4a",
    )
    ax.text(
        3.3,
        0.45,
        "回答合计 62,206 条 · 医生 7,143 位（本地样本；每题只计一次）",
        fontsize=10,
        color="#475569",
    )
    ax.annotate(
        "",
        xy=(2.55, 3.0),
        xytext=(2.3, 3.0),
        arrowprops=dict(arrowstyle="-|>", color="#64748b"),
    )
    ax.set_title("问题 → 患者描述＋多条医生回答", fontsize=13, pad=10, loc="left")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "P3_数据概览_结构示意.png", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    setup_chinese_font()

    scope = pd.read_csv(CASE_SCOPE)
    answers = scope["doctor_answer_count"]
    local = bucket_counts(answers)
    local_median = int(answers.median())
    local_ge2 = float((answers >= 2).mean())

    # Reference values supplied by the user for the full historical set. The
    # full file is not present locally, so these are labelled as unverified.
    reference = pd.DataFrame(
        {
            "bucket": ["1条", "2–4条", "5–9条", "10–19条", "20条及以上"],
            "count": [38151, 29640, 54009, 134802, 21744],
            "share": [
                38151 / 278346,
                29640 / 278346,
                54009 / 278346,
                134802 / 278346,
                21744 / 278346,
            ],
        }
    )

    with (OUT_DIR / "P8_回答数分布.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "bucket",
                "local_question_count",
                "local_share",
                "reference_question_count_unverified",
                "reference_share_unverified",
            ]
        )
        for lrow, rrow in zip(
            local.itertuples(index=False),
            reference.itertuples(index=False),
            strict=False,
        ):
            writer.writerow(
                [
                    lrow.bucket,
                    lrow.count,
                    round(lrow.share, 6),
                    rrow.count,
                    round(rrow.share, 6),
                ]
            )
        writer.writerow([])
        writer.writerow(["指标", "本地样本（已核实）", "参考全量（未核实）", "说明"])
        writer.writerow(
            [
                "独立问题数",
                len(answers),
                278346,
                "每题只计一次；按原始问题分组",
            ]
        )
        writer.writerow(["每题回答数中位数", local_median, 10, "本地 9 条，参考 10 条"])
        writer.writerow(
            [
                "至少两条回答占比",
                round(local_ge2, 6),
                0.863,
                "本地 89.34%，参考 86.3%",
            ]
        )

    bar_chart(
        local,
        len(answers),
        "每题医生回答数分布（本地样本，已核实）",
        "data_app_chain 实核：7,000 个独立问题",
        "P8_回答数分布_本地实核.png",
        local_median,
        local_ge2,
    )
    bar_chart(
        reference,
        278346,
        "每题医生回答数分布（参考全量口径，未核实）",
        "用户提供的参考值：278,346 个问题",
        "P8_回答数分布_参考口径未核实.png",
        10,
        0.863,
    )
    structure_diagram()

    print("local total", len(answers))
    print(local.to_string(index=False))
    print("local median", local_median, "ge2", f"{local_ge2:.4%}")


if __name__ == "__main__":
    main()
