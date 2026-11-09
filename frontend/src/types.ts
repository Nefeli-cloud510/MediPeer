export type MessageRole = 'patient' | 'doctor';

export interface MessageView {
  id: string;
  role: MessageRole;
  speaker_id: string | null;
  speaker_name: string | null;
  speaker_title: string | null;
  hospital: string | null;
  text: string;
  created_at: string | null;
  order_basis: string;
  needs_review: boolean;
  review_reason: string | null;
}

export interface CaseSummary {
  id: string;
  title: string;
  department: string | null;
  platform: string;
  validation_scope: string;
  version_no: number;
  template_key: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface CaseExportView {
  export_id: string;
  case_id: string;
  run_id: string;
  format: 'json' | 'markdown';
  file_name: string;
  content: string;
  created_at: string;
}

export interface DraftView {
  id: string;
  case_id: string;
  current_text: string;
  current_hash: string;
  version_no: number;
  updated_at: string;
}

export interface CaseDetail extends CaseSummary {
  messages: MessageView[];
  draft: DraftView | null;
}

export interface DemoTemplate {
  key: string;
  title: string;
  department: string;
  platform: string;
  validation_scope: string;
}

export interface UserView {
  id: string;
  username: string;
  display_name: string;
  role: string;
}

export interface LoginResponse {
  token: string;
  token_type: string;
  user: UserView;
}

export interface ReplayResponse {
  case_id: string;
  focus_message_id: string;
  cutoff_at: string | null;
  review_context: 'replay' | 'current';
  visible_messages: MessageView[];
  prior_answer_message_ids: string[];
  patient_message_ids: string[];
  excluded_message_ids: string[];
  exclusion_reasons: Record<string, string>;
  order_quality: 'ok' | 'partial' | 'unknown';
}

export type PatientFactStatus =
  | 'confirmed'
  | 'denied'
  | 'uncertain'
  | 'unknown'
  | 'asked_unanswered';

export interface PatientFactEvidenceRef {
  message_id: string;
  role: MessageRole;
  snippet: string;
  kind: 'patient_statement' | 'doctor_question_inference';
}

export interface PatientFactView {
  id: string;
  dimension: string;
  statement: string;
  status: PatientFactStatus;
  time_range: string | null;
  condition: string | null;
  evidence_refs: PatientFactEvidenceRef[];
  checked_message_ids: string[];
}

export interface PatientFactAnalysisResponse {
  run_id: string;
  case_id: string;
  focus_message_id: string;
  cutoff_at: string | null;
  review_context: 'replay' | 'current';
  order_quality: 'ok' | 'partial' | 'unknown';
  source_mode: string;
  model_name: string;
  rules_version: string;
  facts: PatientFactView[];
}

export type DoctorOpinionKind =
  | 'doctor_opinion'
  | 'doctor_question'
  | 'doctor_condition';

export interface DoctorOpinionEvidenceRef {
  message_id: string;
  role: 'doctor';
  snippet: string;
  kind: DoctorOpinionKind;
}

export type OpinionClaimType =
  | 'clarifying_question'
  | 'recommendation'
  | 'general_knowledge'
  | 'patient_fact_statement'
  | 'possible_explanation';

export type OpinionCertainty = 'asserted' | 'conditional' | 'hedged';
export type OpinionPolarity = 'positive' | 'negative';

export type OpinionDraftRelation =
  | 'answered_by_draft'
  | 'unanswered_question'
  | 'diverging'
  | 'conditional_only'
  | 'complementing'
  | 'reinforcing';

export interface DoctorOpinionView {
  id: string;
  source_message_id: string;
  speaker_name: string | null;
  speaker_title: string | null;
  hospital: string | null;
  topic: string;
  claim_type: OpinionClaimType;
  statement: string;
  polarity: OpinionPolarity;
  certainty: OpinionCertainty;
  condition: string | null;
  time_range: string | null;
  draft_relation: OpinionDraftRelation;
  draft_explanation: string;
  evidence_refs: DoctorOpinionEvidenceRef[];
  checked_message_ids: string[];
}

export type PeerRelationType =
  | 'repeat'
  | 'supplement'
  | 'divergence'
  | 'conditional_diff'
  | 'unresolved';

export interface PeerOpinionRelationView {
  id: string;
  source_claim_id: string;
  target_claim_id: string;
  source_message_id: string;
  target_message_id: string;
  relation_type: PeerRelationType;
  divergence_kind: string | null;
  explanation: string;
  applicability_conditions: string | null;
  evidence_refs: DoctorOpinionEvidenceRef[];
}

export interface DoctorOpinionAnalysisResponse {
  run_id: string;
  case_id: string;
  focus_message_id: string;
  cutoff_at: string | null;
  review_context: 'replay' | 'current';
  order_quality: 'ok' | 'partial' | 'unknown';
  source_mode: string;
  model_name: string;
  rules_version: string;
  opinions: DoctorOpinionView[];
  peer_relations: PeerOpinionRelationView[];
}

export type JointContribution =
  | 'new_claim'
  | 'repeats_prior'
  | 'reinforces_prior'
  | 'answers_prior_question'
  | 'unanswered_question'
  | 'diverges_from_prior'
  | 'mixed_prior_views'
  | 'condition_only';

export type JointPatientGrounding =
  | 'patient_stated'
  | 'patient_denied'
  | 'patient_uncertain'
  | 'unmentioned'
  | 'detail_unverified'
  | 'doctor_question_only'
  | 'mixed_temporal'
  | 'professional_recommendation'
  | 'needs_review';

export type JointSuggestedAction =
  | 'keep_and_confirm'
  | 'defer'
  | 'revise_fact_statement'
  | 'clarify_or_revise'
  | 'clarify_condition'
  | 'view_both_sources';

export interface JointDraftSentenceView {
  id: string;
  text: string;
}

export interface JointReviewEvidenceRef {
  message_id: string;
  role: MessageRole;
  snippet: string;
  kind: string;
}

export interface JointFindingView {
  id: string;
  draft_sentence_id: string;
  draft_sentence_text: string;
  contribution: JointContribution;
  patient_grounding: JointPatientGrounding;
  explanation: string;
  evidence_refs: JointReviewEvidenceRef[];
  related_fact_ids: string[];
  related_claim_ids: string[];
  checked_message_ids: string[];
  suggested_action: JointSuggestedAction;
  review_status: string;
  signature: string;
}

export interface JointReviewAnalysisResponse {
  run_id: string;
  case_id: string;
  focus_message_id: string;
  cutoff_at: string | null;
  review_context: 'replay' | 'current';
  order_quality: 'ok' | 'partial' | 'unknown';
  source_mode: string;
  model_name: string;
  rules_version: string;
  draft_text: string;
  draft_sentences: JointDraftSentenceView[];
  findings: JointFindingView[];
  previous_run_id: string | null;
  diff_reason: string | null;
  diff: FindingDiffItem[];
}

export type FindingDiffChange = 'added' | 'resolved' | 'unchanged';

export interface FindingDiffItem {
  signature: string;
  draft_sentence_text: string;
  change: FindingDiffChange;
  old_status: string | null;
  new_status: string;
}

export type ReviewActionKind = 'confirmed' | 'rejected' | 'supplement_adopted';

export interface ReviewActionRequest {
  finding_id: string;
  action: ReviewActionKind;
  note?: string;
  suggested_text?: string;
}

export interface ReviewActionView {
  id: string;
  finding_id: string;
  run_id: string;
  action: string;
  note: string | null;
  suggested_text: string | null;
  supplement_id: string | null;
  created_by: string;
  created_at: string;
}

export interface SupplementRequest {
  content: string;
  finding_id?: string;
  note?: string;
}

export interface SupplementView {
  id: string;
  case_id: string;
  case_version_id: string;
  version_no: number;
  message_id: string;
  finding_id: string | null;
  source_type: string;
  role: string;
  content: string;
  created_by: string;
  created_at: string;
}

export interface DraftUpdateRequest {
  draft_text: string;
  reason?: string;
}

export interface Session {
  token: string;
  user: UserView;
}
