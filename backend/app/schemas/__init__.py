"""Pydantic schemas for MediPeer API responses and requests."""

from .auth import LoginRequest, LoginResponse, UserView
from .case import (
    CaseDetail,
    CaseSummary,
    DraftView,
    MessageView,
    ReplayRequest,
    ReplayResponse,
)
from .doctor_opinions import (
    DoctorOpinionAnalysisRequest,
    DoctorOpinionAnalysisResponse,
    DoctorOpinionEvidenceRef,
    DoctorOpinionView,
    PeerOpinionRelationView,
)
from .exports import CaseExportView
from .joint_review import (
    JointDraftSentenceView,
    JointFindingView,
    JointReviewAnalysisRequest,
    JointReviewAnalysisResponse,
    JointReviewEvidenceRef,
)
from .patient_facts import (
    PatientFactAnalysisRequest,
    PatientFactAnalysisResponse,
    PatientFactEvidenceRef,
    PatientFactView,
)
from .review_actions import (
    DraftUpdateRequest,
    FindingDiffItem,
    ReviewActionRequest,
    ReviewActionView,
    SupplementRequest,
    SupplementView,
)

__all__ = [
    "CaseDetail",
    "CaseSummary",
    "CaseExportView",
    "DraftUpdateRequest",
    "DoctorOpinionAnalysisRequest",
    "DoctorOpinionAnalysisResponse",
    "DoctorOpinionEvidenceRef",
    "DoctorOpinionView",
    "DraftView",
    "FindingDiffItem",
    "JointDraftSentenceView",
    "JointFindingView",
    "JointReviewAnalysisRequest",
    "JointReviewAnalysisResponse",
    "JointReviewEvidenceRef",
    "LoginRequest",
    "LoginResponse",
    "MessageView",
    "PatientFactAnalysisRequest",
    "PatientFactAnalysisResponse",
    "PatientFactEvidenceRef",
    "PatientFactView",
    "PeerOpinionRelationView",
    "ReplayRequest",
    "ReplayResponse",
    "ReviewActionRequest",
    "ReviewActionView",
    "SupplementRequest",
    "SupplementView",
    "UserView",
]
