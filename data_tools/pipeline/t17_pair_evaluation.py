"""t17: 完整方案配对评测 CLI。

同一演示病例、同一焦点、同一草稿版本、同一严格前序上下文下，
分别运行完整方案（joint-review）与同模型直接审阅基线（baseline-review），
生成 evaluation_summary.json / evaluation_detail.csv / failure_cases.md。

示例：

    python data_tools/pipeline/t17_pair_evaluation.py \
        --template demo_respiratory_multi_doctor
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db.models import Base
from backend.app.db.models import User
from backend.app.db.session import SessionLocal, configure_database, get_engine
from backend.app.services.auth_service import ensure_seed_users
from backend.app.services.evaluation_service import (
    PairEvaluationError,
    run_pair_evaluation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MediPeer t17 完整方案配对评测")
    parser.add_argument(
        "--template",
        default="demo_respiratory_multi_doctor",
        help="演示模板 key（默认为 demo_respiratory_multi_doctor）",
    )
    parser.add_argument(
        "--focus",
        default=None,
        help="焦点回答消息 id；缺省时使用模板约定的焦点",
    )
    parser.add_argument(
        "--name",
        default="t17 完整方案配对评测",
        help="评测名称，写入 EvaluationRun",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="评测数据库 URL；缺省使用隔离的 data_app_chain/eval_db/*.db，"
        "不写入正在运行的应用数据库",
    )
    args = parser.parse_args()

    if args.db_url is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        eval_db = ROOT / "data_app_chain" / "eval_db" / f"eval_{stamp}.db"
        eval_db.parent.mkdir(parents=True, exist_ok=True)
        args.db_url = f"sqlite:///{eval_db.as_posix()}"
    configure_database(args.db_url)
    Base.metadata.create_all(bind=get_engine())
    with SessionLocal() as db:
        ensure_seed_users(db)
        from sqlalchemy import select

        user = db.scalar(select(User).where(User.username == "demo"))
        if user is None:
            print("demo user missing after seeding", file=sys.stderr)
            return 2
        try:
            eval_run, output_dir = run_pair_evaluation(
                db,
                user=user,
                template_key=args.template,
                focus_message_id=args.focus,
                name=args.name,
            )
        except (PairEvaluationError, PermissionError, ValueError, KeyError) as exc:
            print(f"evaluation failed: {exc}", file=sys.stderr)
            return 1

    print(f"evaluation_run_id: {eval_run.id}")
    print(f"status: {eval_run.status}")
    print(f"output_dir: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
