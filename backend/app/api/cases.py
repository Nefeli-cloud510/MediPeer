from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db.models import CaseVersion, User
from ..db.session import get_db
from ..demo import get_demo_template, list_demo_templates
from ..schemas import (
    CaseExportView,
    CaseDetail,
    CaseSummary,
    DraftUpdateRequest,
    DoctorOpinionAnalysisRequest,
    DoctorOpinionAnalysisResponse,
    DraftView,
    JointReviewAnalysisRequest,
    JointReviewAnalysisResponse,
    PatientFactAnalysisRequest,
    PatientFactAnalysisResponse,
    ReplayRequest,
    ReplayResponse,
    ReviewActionRequest,
    ReviewActionView,
    SupplementRequest,
    SupplementView,
)
from ..services import (
    export_service,
    baseline_review_service,
    case_service,
    doctor_opinions_service,
    joint_review_service,
    patient_facts_service,
    review_action_service,
    scope_service,
)
from .deps import get_current_user


router = APIRouter(prefix="/api", tags=["cases"])


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=message)


@router.get("/demo/templates")
def demo_templates() -> list[dict[str, str]]:
    return [
        {
            "key": t["key"],
            "title": t["title"],
            "department": t.get("department", ""),
            "platform": t.get("platform", "weiyi"),
            "validation_scope": t.get("validation_scope", "generic_text"),
        }
        for t in list_demo_templates()
    ]


@router.get("/cases", response_model=list[CaseSummary])
def list_my_cases(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CaseSummary]:
    return case_service.list_cases(db, user)


@router.post("/cases/demo/{template_key}", response_model=CaseDetail)
def create_demo_case(
    template_key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseDetail:
    try:
        case = case_service.create_demo_case(db, user, template_key)
    except KeyError:
        raise _not_found(f"unknown demo template: {template_key}")
    return case_service.get_case_detail(db, case.id, user)


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(
    case_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseDetail:
    try:
        return case_service.get_case_detail(db, case_id, user)
    except PermissionError:
        raise _not_found("case not found")


@router.get("/cases/{case_id}/draft", response_model=DraftView)
def get_draft(
    case_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DraftView:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
    except PermissionError:
        raise _not_found("case not found")
    draft = case_service.get_draft_for_case(db, case)
    if draft is None:
        raise _not_found("draft not found")
    return case_service.draft_view(db, draft)


@router.get("/cases/{case_id}/exports", response_model=CaseExportView)
def get_case_export(
    case_id: str,
    run_id: str,
    format: str = "json",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseExportView:
    fmt = format if format in ("json", "markdown") else ""
    if fmt not in ("json", "markdown"):
        raise HTTPException(status_code=400, detail="format must be json or markdown")
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return export_service.build_case_export(
            db,
            case,
            run_id,
            user,
            fmt,  # type: ignore[arg-type]
        )
    except PermissionError:
        raise _not_found("case not found")
    except KeyError:
        raise _not_found("run not found in this case")


@router.post("/cases/{case_id}/review-actions", response_model=ReviewActionView)
def create_review_action(
    case_id: str,
    body: ReviewActionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewActionView:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return review_action_service.record_review_action(
            db,
            case,
            user,
            body.model_dump(),
        )
    except PermissionError:
        raise _not_found("case not found")
    except KeyError:
        raise _not_found("finding not found in this case")


@router.post("/cases/{case_id}/supplements", response_model=SupplementView)
def create_supplement(
    case_id: str,
    body: SupplementRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SupplementView:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return review_action_service.add_supplement(
            db,
            case,
            user,
            body.model_dump(),
        )
    except PermissionError:
        raise _not_found("case not found")
    except KeyError:
        raise _not_found("finding not found in this case")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.put("/cases/{case_id}/draft", response_model=DraftView)
def update_draft(
    case_id: str,
    body: DraftUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DraftView:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return review_action_service.update_draft(
            db,
            case,
            user,
            body.model_dump(),
        )
    except PermissionError:
        raise _not_found("case not found")


@router.post("/cases/{case_id}/replay", response_model=ReplayResponse)
def replay_case(
    case_id: str,
    body: ReplayRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReplayResponse:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
    except PermissionError:
        raise _not_found("case not found")
    version = db.get(CaseVersion, case.current_version_id)
    if version is None:
        raise HTTPException(status_code=409, detail="case has no current version")
    try:
        scope = scope_service.compute_replay_scope(
            case_id,
            version.messages_json,
            body.focus_message_id,
            body.review_context,
        )
    except KeyError:
        raise _not_found("focus message not found in this case")
    return ReplayResponse(
        case_id=scope.case_id,
        focus_message_id=scope.focus_message_id,
        cutoff_at=scope.cutoff_at,
        review_context=scope.review_context,
        visible_messages=scope.visible_messages,
        prior_answer_message_ids=scope.prior_answer_message_ids,
        patient_message_ids=scope.patient_message_ids,
        excluded_message_ids=scope.excluded_message_ids,
        exclusion_reasons=scope.exclusion_reasons,
        order_quality=scope.order_quality,
    )


@router.post(
    "/cases/{case_id}/patient-facts",
    response_model=PatientFactAnalysisResponse,
)
def analyze_patient_facts(
    case_id: str,
    body: PatientFactAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientFactAnalysisResponse:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return patient_facts_service.run_patient_fact_analysis(
            db,
            case,
            body.focus_message_id,
            body.review_context,
        )
    except PermissionError:
        raise _not_found("case not found")
    except KeyError:
        raise _not_found("focus message not found in this case")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post(
    "/cases/{case_id}/doctor-opinions",
    response_model=DoctorOpinionAnalysisResponse,
)
def analyze_doctor_opinions(
    case_id: str,
    body: DoctorOpinionAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DoctorOpinionAnalysisResponse:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return doctor_opinions_service.run_doctor_opinion_analysis(
            db,
            case,
            body.focus_message_id,
            body.review_context,
        )
    except PermissionError:
        raise _not_found("case not found")
    except KeyError:
        raise _not_found("focus message not found in this case")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post(
    "/cases/{case_id}/joint-review",
    response_model=JointReviewAnalysisResponse,
)
def analyze_joint_review(
    case_id: str,
    body: JointReviewAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JointReviewAnalysisResponse:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return joint_review_service.run_joint_review(
            db,
            case,
            body.focus_message_id,
            body.review_context,
        )
    except PermissionError:
        raise _not_found("case not found")
    except KeyError:
        raise _not_found("focus message not found in this case")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except joint_review_service.JointReviewFailure as exc:
        raise HTTPException(
            status_code=502,
            detail={"run_id": exc.run_id, "error_class": exc.error_class, "error": str(exc)},
        )


@router.post(
    "/cases/{case_id}/baseline-review",
    response_model=JointReviewAnalysisResponse,
)
def analyze_baseline_review(
    case_id: str,
    body: JointReviewAnalysisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JointReviewAnalysisResponse:
    try:
        case = case_service.get_case_for_user(db, case_id, user)
        return baseline_review_service.run_baseline_review(
            db,
            case,
            body.focus_message_id,
            body.review_context,
        )
    except PermissionError:
        raise _not_found("case not found")
    except KeyError:
        raise _not_found("focus message not found in this case")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except baseline_review_service.BaselineReviewFailure as exc:
        raise HTTPException(
            status_code=502,
            detail={"run_id": exc.run_id, "error_class": exc.error_class, "error": str(exc)},
        )
