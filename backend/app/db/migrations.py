from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


COMPAT_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "analysis_runs": [
        ("review_mode", "VARCHAR(40) DEFAULT 'joint_review'"),
    ],
    "review_actions": [
        ("supplement_id", "VARCHAR(32)"),
        ("suggested_text", "TEXT"),
    ],
    "supplements": [
        ("finding_id", "VARCHAR(32)"),
    ],
    "model_calls": [
        ("prompt_version", "VARCHAR(120)"),
        ("generation_config", "TEXT"),
        ("prompt_text", "TEXT"),
        ("output_text", "TEXT"),
    ],
}


def ensure_compat_columns(engine: Engine) -> None:
    """Add columns introduced by newer code to databases created by older code."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table_name, columns in COMPAT_COLUMNS.items():
            if table_name not in existing_tables:
                continue
            existing = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for column_name, ddl in columns:
                if column_name not in existing:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"
                        )
                    )
