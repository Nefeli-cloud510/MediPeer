from __future__ import annotations

from backend.app.db.session import build_engine


def test_file_engine_enables_wal(tmp_path):
    db_path = tmp_path / "medipeer_test.db"
    engine = build_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
            assert mode == "wal"
    finally:
        engine.dispose()


def test_memory_engine_still_works():
    engine = build_engine("sqlite:///:memory:")
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT 1").scalar() == 1
    finally:
        engine.dispose()
