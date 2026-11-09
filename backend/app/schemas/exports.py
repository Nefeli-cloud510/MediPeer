from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CaseExportView(BaseModel):
    export_id: str
    case_id: str
    run_id: str
    format: Literal["json", "markdown"]
    file_name: str
    content: str
    created_at: str
