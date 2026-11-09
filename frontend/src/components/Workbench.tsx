import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  ArrowUpRight,
  CheckCircle2,
  ClipboardPlus,
  Clock3,
  Download,
  FileText,
  Loader2,
  LogOut,
  MessageSquareText,
  Pencil,
  RefreshCw,
  Save,
  ScanSearch,
  Send,
  Stethoscope,
  UserPlus,
  UserRound,
  XCircle,
} from 'lucide-react';

import { api, errorMessage } from '../api';
import {
  diffChangeLabel,
  diffReasonLabel,
  formatShortTime,
  formatTime,
  jointActionLabel,
  jointContributionLabel,
  jointEvidenceKindLabel,
  jointGroundingLabel,
  opinionCertaintyLabel,
  opinionClaimTypeLabel,
  opinionDraftRelationLabel,
  opinionPolarityLabel,
  peerRelationKindLabel,
  peerRelationTypeLabel,
  patientFactDimensionLabel,
  patientFactStatusLabel,
  orderQualityLabel,
  platformLabel,
  reviewStatusLabel,
  scopeLabel,
} from '../labels';
import type {
  CaseDetail,
  DoctorOpinionAnalysisResponse,
  DoctorOpinionView,
  JointFindingView,
  JointReviewAnalysisResponse,
  MessageView,
  PatientFactAnalysisResponse,
  PeerOpinionRelationView,
  ReplayResponse,
  UserView,
} from '../types';

interface WorkbenchProps {
  caseId: string;
  user: UserView;
  onBack: () => void;
  onLogout: () => void;
}

const FOCUS_KEY_PREFIX = 'medipeer.focus.';

function focusKey(caseId: string): string {
  return `${FOCUS_KEY_PREFIX}${caseId}`;
}

function parseTime(value: string | null): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function sortMessages(messages: MessageView[]): MessageView[] {
  return [...messages].sort((a, b) => {
    const diff = parseTime(a.created_at) - parseTime(b.created_at);
    if (diff !== 0) return diff;
    return a.id.localeCompare(b.id);
  });
}

function pickDefaultFocus(messages: MessageView[]): string | null {
  const doctors = messages.filter((message) => message.role === 'doctor');
  if (doctors.length === 0) return null;
  const marked = doctors.find((message) => /focus|焦点/i.test(message.id));
  if (marked) return marked.id;

  const sortable = doctors
    .map((message) => ({ message, time: parseTime(message.created_at) }))
    .sort((a, b) => a.time - b.time);
  for (let index = 0; index < sortable.length; index += 1) {
    const current = sortable[index].time;
    if (sortable.some((item) => item.time > current)) {
      return sortable[index].message.id;
    }
  }
  return sortable[sortable.length - 1].message.id;
}

export function Workbench({ caseId, user, onBack, onLogout }: WorkbenchProps) {
  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [replay, setReplay] = useState<ReplayResponse | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState('');
  const [patientFacts, setPatientFacts] =
    useState<PatientFactAnalysisResponse | null>(null);
  const [factsBusy, setFactsBusy] = useState(false);
  const [factsError, setFactsError] = useState('');
  const [doctorOpinions, setDoctorOpinions] =
    useState<DoctorOpinionAnalysisResponse | null>(null);
  const [opinionsBusy, setOpinionsBusy] = useState(false);
  const [opinionsError, setOpinionsError] = useState('');
  const [jointReview, setJointReview] =
    useState<JointReviewAnalysisResponse | null>(null);
  const [jointBusy, setJointBusy] = useState(false);
  const [jointError, setJointError] = useState('');
  const [actionBusyId, setActionBusyId] = useState<string | null>(null);
  const [actionBusyKind, setActionBusyKind] = useState<
    'confirmed' | 'rejected' | 'supplement_adopted' | null
  >(null);
  const [actionError, setActionError] = useState<Record<string, string>>({});
  const [adoptOpenId, setAdoptOpenId] = useState<string | null>(null);
  const [adoptText, setAdoptText] = useState('');
  const [draftEditing, setDraftEditing] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [draftReason, setDraftReason] = useState('');
  const [draftSaving, setDraftSaving] = useState(false);
  const [draftError, setDraftError] = useState('');
  const [supplementOpen, setSupplementOpen] = useState(false);
  const [supplementText, setSupplementText] = useState('');
  const [supplementFindingId, setSupplementFindingId] = useState('');
  const [supplementNote, setSupplementNote] = useState('');
  const [supplementSaving, setSupplementSaving] = useState(false);
  const [supplementError, setSupplementError] = useState('');
  const [exportSaving, setExportSaving] = useState<'json' | 'markdown' | null>(
    null,
  );
  const [exportError, setExportError] = useState('');
  const [focusId, setFocusId] = useState('');
  const [mode, setMode] = useState<'scope' | 'full'>('scope');
  const [reviewContext, setReviewContext] = useState<'replay' | 'current'>(
    'replay',
  );
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const highlightTimer = useRef<number | null>(null);
  const factsRequestRef = useRef(0);
  const opinionsRequestRef = useRef(0);
  const jointRequestRef = useRef(0);
  const messageRefs = useRef<Record<string, HTMLElement | null>>({});
  const draftRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError('');
    setCaseData(null);
    setReplay(null);
    setReplayError('');
    setDoctorOpinions(null);
    setOpinionsBusy(false);
    setOpinionsError('');
    setJointReview(null);
    setJointBusy(false);
    setJointError('');
    api
      .getCase(caseId)
      .then(async (detail) => {
        if (cancelled) return;
        setCaseData(detail);
        const doctors = detail.messages.filter((message) => message.role === 'doctor');
        const stored = localStorage.getItem(focusKey(caseId));
        const initial =
          stored && doctors.some((message) => message.id === stored)
            ? stored
            : pickDefaultFocus(detail.messages);
        if (!initial) return;
        setFocusId(initial);
        try {
          const result = await api.replay(caseId, initial);
          if (!cancelled) {
            setReplay(result);
            void loadPatientFacts(initial, 'replay');
            void loadDoctorOpinions(initial, 'replay');
            void loadJointReview(initial, 'replay');
          }
        } catch (err) {
          if (!cancelled) setReplayError(errorMessage(err));
        }
      })
      .catch((err) => {
        if (!cancelled) setLoadError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [caseId, attempt]);

  useEffect(() => {
    return () => {
      if (highlightTimer.current) window.clearTimeout(highlightTimer.current);
    };
  }, []);

  const allMessages = useMemo(
    () => sortMessages(caseData?.messages ?? []),
    [caseData],
  );
  const doctorMessages = useMemo(
    () => allMessages.filter((message) => message.role === 'doctor'),
    [allMessages],
  );
  const messageById = useMemo(
    () => new Map(allMessages.map((message) => [message.id, message])),
    [allMessages],
  );
  const visibleIdSet = useMemo(
    () => new Set(replay?.visible_messages.map((message) => message.id) ?? []),
    [replay],
  );
  const renderMessages =
    mode === 'scope' && replay ? replay.visible_messages : allMessages;
  const reviewDone = Boolean(jointReview && patientFacts && doctorOpinions);
  const reviewBusy = Boolean(jointBusy || factsBusy || opinionsBusy);
  const reviewFailed = Boolean(jointError || factsError || opinionsError);

  async function runReplay(id: string, context: 'replay' | 'current') {
    setReviewContext(context);
    factsRequestRef.current += 1;
    opinionsRequestRef.current += 1;
    jointRequestRef.current += 1;
    setPatientFacts(null);
    setFactsBusy(false);
    setFactsError('');
    setDoctorOpinions(null);
    setOpinionsBusy(false);
    setOpinionsError('');
    setJointReview(null);
    setJointBusy(false);
    setJointError('');
    setReplayBusy(true);
    setReplayError('');
    setReplay(null);
    try {
      const result = await api.replay(caseId, id, context);
      setReplay(result);
      void loadPatientFacts(id, context);
      void loadDoctorOpinions(id, context);
      void loadJointReview(id, context);
    } catch (err) {
      setReplayError(errorMessage(err));
    } finally {
      setReplayBusy(false);
    }
  }

  async function loadPatientFacts(
    focusMessageId: string,
    context: 'replay' | 'current' = 'replay',
  ) {
    const requestId = ++factsRequestRef.current;
    setFactsBusy(true);
    setFactsError('');
    setPatientFacts(null);
    try {
      const result = await api.analyzePatientFacts(
        caseId,
        focusMessageId,
        context,
      );
      if (requestId === factsRequestRef.current) {
        setPatientFacts(result);
      }
    } catch (err) {
      if (requestId === factsRequestRef.current) {
        setFactsError(errorMessage(err));
      }
    } finally {
      if (requestId === factsRequestRef.current) {
        setFactsBusy(false);
      }
    }
  }

  async function loadDoctorOpinions(
    focusMessageId: string,
    context: 'replay' | 'current' = 'replay',
  ) {
    const requestId = ++opinionsRequestRef.current;
    setOpinionsBusy(true);
    setOpinionsError('');
    setDoctorOpinions(null);
    try {
      const result = await api.analyzeDoctorOpinions(
        caseId,
        focusMessageId,
        context,
      );
      if (requestId === opinionsRequestRef.current) {
        setDoctorOpinions(result);
      }
    } catch (err) {
      if (requestId === opinionsRequestRef.current) {
        setOpinionsError(errorMessage(err));
      }
    } finally {
      if (requestId === opinionsRequestRef.current) {
        setOpinionsBusy(false);
      }
    }
  }

  async function loadJointReview(
    focusMessageId: string,
    context: 'replay' | 'current' = 'replay',
  ) {
    const requestId = ++jointRequestRef.current;
    setJointBusy(true);
    setJointError('');
    setExportError('');
    setJointReview(null);
    try {
      const result = await api.analyzeJointReview(
        caseId,
        focusMessageId,
        context,
      );
      if (requestId === jointRequestRef.current) {
        setJointReview(result);
      }
    } catch (err) {
      if (requestId === jointRequestRef.current) {
        setJointError(errorMessage(err));
      }
    } finally {
      if (requestId === jointRequestRef.current) {
        setJointBusy(false);
      }
    }
  }

  async function downloadExport(format: 'json' | 'markdown') {
    if (!replay || !jointReview) return;
    setExportSaving(format);
    setExportError('');
    try {
      const result = await api.exportCase(caseId, jointReview.run_id, format);
      const blob = new Blob([result.content], {
        type:
          format === 'json'
            ? 'application/json;charset=utf-8'
            : 'text/markdown;charset=utf-8',
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = result.file_name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(errorMessage(err));
    } finally {
      setExportSaving(null);
    }
  }

  function updateFindingStatus(findingId: string, status: string) {
    setJointReview((previous) =>
      previous
        ? {
            ...previous,
            findings: previous.findings.map((finding) =>
              finding.id === findingId
                ? { ...finding, review_status: status }
                : finding,
            ),
          }
        : previous,
    );
  }

  async function refreshCase(
    preferredFocusId?: string,
    context: 'replay' | 'current' = 'replay',
  ) {
    const detail = await api.getCase(caseId);
    setCaseData(detail);
    const doctors = detail.messages.filter((message) => message.role === 'doctor');
    const target =
      preferredFocusId && doctors.some((message) => message.id === preferredFocusId)
        ? preferredFocusId
        : focusId;
    const resolved =
      doctors.some((message) => message.id === target)
        ? target
        : pickDefaultFocus(detail.messages);
    if (!resolved) return;
    if (resolved !== focusId) {
      localStorage.setItem(focusKey(caseId), resolved);
    }
    setFocusId(resolved);
    await runReplay(resolved, context);
  }

  async function switchReviewContext(context: 'replay' | 'current') {
    if (!focusId || context === reviewContext) return;
    await runReplay(focusId, context);
  }

  async function submitFindingAction(
    finding: JointFindingView,
    action: 'confirmed' | 'rejected',
  ) {
    setActionBusyId(finding.id);
    setActionBusyKind(action);
    setActionError((previous) => ({ ...previous, [finding.id]: '' }));
    try {
      await api.submitReviewAction(caseId, { finding_id: finding.id, action });
      updateFindingStatus(
        finding.id,
        action === 'confirmed' ? 'confirmed' : 'rejected',
      );
    } catch (err) {
      setActionError((previous) => ({
        ...previous,
        [finding.id]: errorMessage(err),
      }));
    } finally {
      setActionBusyId(null);
      setActionBusyKind(null);
    }
  }

  async function submitAdopted(finding: JointFindingView) {
    const content = adoptText.trim();
    if (!content) return;
    setActionBusyId(finding.id);
    setActionBusyKind('supplement_adopted');
    setActionError((previous) => ({ ...previous, [finding.id]: '' }));
    try {
      await api.submitReviewAction(caseId, {
        finding_id: finding.id,
        action: 'supplement_adopted',
        suggested_text: content,
      });
      await api.addSupplement(caseId, {
        content,
        finding_id: finding.id,
        note: '采用补问时人工补录患者答复',
      });
      updateFindingStatus(finding.id, 'question_adopted');
      setAdoptOpenId(null);
      setAdoptText('');
      await refreshCase(focusId, 'current');
    } catch (err) {
      setActionError((previous) => ({
        ...previous,
        [finding.id]: errorMessage(err),
      }));
    } finally {
      setActionBusyId(null);
      setActionBusyKind(null);
    }
  }

  async function saveSupplement() {
    const content = supplementText.trim();
    if (!content) return;
    const payload = {
      content,
      finding_id: supplementFindingId || undefined,
      note: supplementNote.trim() || undefined,
    };
    setSupplementSaving(true);
    setSupplementError('');
    try {
      await api.addSupplement(caseId, payload);
      setSupplementOpen(false);
      setSupplementText('');
      setSupplementFindingId('');
      setSupplementNote('');
      await refreshCase(focusId, 'current');
    } catch (err) {
      setSupplementError(errorMessage(err));
    } finally {
      setSupplementSaving(false);
    }
  }

  async function saveDraft() {
    const text = draftText.trim();
    if (!text) return;
    const payload = {
      draft_text: text,
      reason: draftReason.trim() || undefined,
    };
    setDraftSaving(true);
    setDraftError('');
    try {
      await api.updateDraft(caseId, payload);
      setDraftEditing(false);
      await refreshCase(focusId, reviewContext);
    } catch (err) {
      setDraftError(errorMessage(err));
    } finally {
      setDraftSaving(false);
    }
  }

  function handleFocusChange(id: string) {
    setFocusId(id);
    localStorage.setItem(focusKey(caseId), id);
    void runReplay(id, 'replay');
  }

  function locateMessage(id: string) {
    if (replay && !visibleIdSet.has(id)) {
      setMode('full');
    }
    setHighlightId(id);
    window.setTimeout(() => {
      messageRefs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
    if (highlightTimer.current) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlightId(null), 2600);
  }

  function locateDraftSentence(id: string) {
    setHighlightId(`draft:${id}`);
    window.setTimeout(() => {
      draftRefs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
    if (highlightTimer.current) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlightId(null), 2600);
  }

  function evidencePills(
    refs: Array<{
      message_id: string;
      role: 'patient' | 'doctor';
      snippet: string;
      kind?: string;
    }>,
  ) {
    if (refs.length === 0) {
      return <span className="meta-text">没有可定位的原文证据</span>;
    }
    return (
      <div className="fact-evidence">
        {refs.map((ref) => (
          <button
            key={`${ref.message_id}-${ref.kind ?? 'ref'}-${ref.snippet}`}
            className="evidence-pill"
            onClick={() => locateMessage(ref.message_id)}
          >
            <MessageSquareText size={12} />
            <span className="evidence-role">
              {ref.role === 'patient' ? '患者' : '医生'}
            </span>
            {ref.kind && (
              <span className="evidence-kind">
                {jointEvidenceKindLabel(ref.kind)}
              </span>
            )}
            <span className="evidence-snippet">{ref.snippet}</span>
            <ArrowUpRight size={12} />
          </button>
        ))}
      </div>
    );
  }

  function opinionCard(opinion: DoctorOpinionView) {
    return (
      <article key={opinion.id} className="opinion-item">
        <div className="opinion-top">
          <span className="opinion-topic">{opinion.topic}</span>
          <span className={`opinion-chip opinion-type-${opinion.claim_type}`}>
            {opinionClaimTypeLabel(opinion.claim_type)}
          </span>
          <span className={`opinion-chip opinion-certainty-${opinion.certainty}`}>
            {opinionCertaintyLabel(opinion.certainty)}
          </span>
          <span className={`opinion-chip opinion-polarity-${opinion.polarity}`}>
            {opinionPolarityLabel(opinion.polarity)}
          </span>
        </div>
        <p className="opinion-statement">{opinion.statement}</p>
        {opinion.condition && (
          <p className="fact-condition">适用条件：{opinion.condition}</p>
        )}
        <div className="opinion-meta">
          <strong>{opinion.speaker_name || '未署名医生'}</strong>
          {opinion.speaker_title && (
            <span className="meta-text">{opinion.speaker_title}</span>
          )}
          {opinion.hospital && (
            <span className="meta-text">{opinion.hospital}</span>
          )}
          {opinion.time_range && (
            <span className="meta-text">时间范围：{opinion.time_range}</span>
          )}
        </div>
        <div className={`draft-relation draft-relation-${opinion.draft_relation}`}>
          <span className="draft-relation-label">
            {opinionDraftRelationLabel(opinion.draft_relation)}
          </span>
          <span className="draft-relation-text">{opinion.draft_explanation}</span>
        </div>
        {evidencePills(opinion.evidence_refs)}
      </article>
    );
  }

  function relationCard(relation: PeerOpinionRelationView) {
    const source = messageById.get(relation.source_message_id);
    const target = messageById.get(relation.target_message_id);
    return (
      <article
        key={relation.id}
        className={`relation-item relation-${relation.relation_type}`}
      >
        <div className="relation-top">
          <span className="relation-chip">
            {peerRelationTypeLabel(relation.relation_type)}
          </span>
          {relation.divergence_kind && (
            <span className="relation-kind">
              {peerRelationKindLabel(relation.divergence_kind)}
            </span>
          )}
          <span className="relation-doctors">
            {source?.speaker_name || '前序医生'} ↔{' '}
            {target?.speaker_name || '前序医生'}
          </span>
        </div>
        <p className="relation-explanation">{relation.explanation}</p>
        {relation.applicability_conditions && (
          <p className="fact-condition">{relation.applicability_conditions}</p>
        )}
        {evidencePills(relation.evidence_refs)}
      </article>
    );
  }

  function findingCard(finding: JointFindingView) {
    const actionBusy = actionBusyId === finding.id;
    const canAdopt =
      finding.contribution === 'unanswered_question' ||
      finding.review_status === 'question_adopted';
    const status = finding.review_status;
    return (
      <article
        key={finding.id}
        className={`joint-finding joint-${finding.contribution} ground-${finding.patient_grounding}`}
      >
        <div className="joint-top">
          <span className="joint-chip joint-chip-contribution">
            {jointContributionLabel(finding.contribution)}
          </span>
          <span className="joint-chip joint-chip-grounding">
            {jointGroundingLabel(finding.patient_grounding)}
          </span>
          <span className="joint-chip joint-chip-action">
            {jointActionLabel(finding.suggested_action)}
          </span>
          <span className={`review-status review-status-${status}`}>
            {reviewStatusLabel(status)}
          </span>
        </div>
        <div className="joint-sentence">
          <span className="joint-sentence-label">草稿句</span>
          <span className="joint-sentence-text">{finding.draft_sentence_text}</span>
          <button
            className="joint-locate"
            onClick={() => locateDraftSentence(finding.draft_sentence_id)}
          >
            <ScanSearch size={12} />
            定位草稿
          </button>
        </div>
        <p className="joint-explanation">{finding.explanation}</p>
        {evidencePills(finding.evidence_refs)}
        <div className="joint-meta">
          关联患者事实 {finding.related_fact_ids.length} 项 · 关联观点{' '}
          {finding.related_claim_ids.length} 条 · 已核查消息{' '}
          {finding.checked_message_ids.length} 条
        </div>
        <div className="joint-actions">
          <button
            className="btn btn-sm btn-ok"
            disabled={actionBusy || status === 'confirmed'}
            onClick={() => void submitFindingAction(finding, 'confirmed')}
          >
            {actionBusy && actionBusyKind === 'confirmed' ? (
              <Loader2 className="spin" size={12} />
            ) : (
              <CheckCircle2 size={12} />
            )}
            确认
          </button>
          <button
            className="btn btn-sm btn-danger"
            disabled={actionBusy || status === 'rejected'}
            onClick={() => void submitFindingAction(finding, 'rejected')}
          >
            {actionBusy && actionBusyKind === 'rejected' ? (
              <Loader2 className="spin" size={12} />
            ) : (
              <XCircle size={12} />
            )}
            驳回
          </button>
          {canAdopt && (
            <button
              className="btn btn-sm btn-amber"
              disabled={actionBusy}
              onClick={() => {
                setAdoptOpenId((current) =>
                  current === finding.id ? null : finding.id,
                );
                setAdoptText('');
              }}
            >
              <ClipboardPlus size={12} />
              采用补问
            </button>
          )}
        </div>
        {actionError[finding.id] && (
          <div className="inline-error">
            <AlertCircle size={14} />
            {actionError[finding.id]}
          </div>
        )}
        {adoptOpenId === finding.id && (
          <div className="adopt-box">
            <label className="adopt-label">补录患者答复</label>
            <textarea
              className="supplement-textarea"
              value={adoptText}
              disabled={actionBusy}
              placeholder="按患者原话补录对此问题的答复，保存后生成新病例版本并重新分析。"
              onChange={(event) => setAdoptText(event.target.value)}
            />
            <div className="inline-actions">
              <button
                className="btn btn-sm btn-primary"
                disabled={actionBusy || !adoptText.trim()}
                onClick={() => void submitAdopted(finding)}
              >
                {actionBusy && actionBusyKind === 'supplement_adopted' ? (
                  <Loader2 className="spin" size={12} />
                ) : (
                  <Send size={12} />
                )}
                采用并重分析
              </button>
              <button
                className="btn btn-sm"
                disabled={actionBusy}
                onClick={() => {
                  setAdoptOpenId(null);
                  setAdoptText('');
                }}
              >
                取消
              </button>
            </div>
          </div>
        )}
      </article>
    );
  }

  function messageCard(message: MessageView) {
    const isFocus = message.id === focusId;
    const excludedReason = replay?.exclusion_reasons[message.id];
    const inVisible = visibleIdSet.has(message.id);
    const classes = [
      'message-item',
      message.role === 'patient' ? 'message-patient' : 'message-doctor',
      isFocus ? 'is-focus' : '',
      mode === 'scope' && replay && !inVisible ? 'is-out-of-scope' : '',
      highlightId === message.id ? 'is-highlighted' : '',
    ]
      .filter(Boolean)
      .join(' ');

    return (
      <article
        key={message.id}
        ref={(element) => {
          messageRefs.current[message.id] = element;
        }}
        className={classes}
      >
        <div className="message-main">
          <div className="message-meta">
            <span className={`role-chip role-${message.role}`}>
              {message.role === 'patient' ? (
                <UserRound size={12} />
              ) : (
                <Stethoscope size={12} />
              )}
              {message.role === 'patient' ? '患者' : '医生'}
            </span>
            <strong>
              {message.speaker_name ||
                (message.role === 'patient' ? '患者' : '未署名医生')}
            </strong>
            {message.speaker_title && (
              <span className="meta-text">{message.speaker_title}</span>
            )}
            {message.hospital && <span className="meta-text">{message.hospital}</span>}
          </div>
          <div className="message-time">
            <Clock3 size={12} />
            <span>{formatTime(message.created_at)}</span>
            <span className="meta-text">
              ·
              {message.order_basis === 'source_timestamp'
                ? '来源时间'
                : message.order_basis || '顺序待核查'}
            </span>
          </div>
          <p className="message-text">{message.text}</p>
          <div className="message-flags">
            {isFocus && <span className="flag flag-focus">焦点回答</span>}
            {replay && !isFocus && !excludedReason && (
              <span className="flag flag-visible">焦点时点前可见</span>
            )}
            {excludedReason && <span className="flag flag-excluded">{excludedReason}</span>}
            {message.needs_review && (
              <span className="flag flag-review">
                待核查：{message.review_reason || '未说明'}
              </span>
            )}
          </div>
        </div>
      </article>
    );
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-left">
          <button className="btn btn-ghost btn-sm" onClick={onBack}>
            <ArrowLeft size={14} />
            案例库
          </button>
          <div className="brand">
            <Stethoscope size={18} />
            医知协同
            <span>MediPeer</span>
          </div>
        </div>
        <div className="topbar-right">
          <span className="user-chip">
            {user.display_name || user.username} · {user.role}
          </span>
          <button className="btn btn-ghost btn-sm" onClick={onLogout}>
            <LogOut size={14} />
            退出
          </button>
        </div>
      </header>

      <main className="workbench">
        {loading ? (
          <div className="workbench-state">
            <div className="state-box">
              <Loader2 className="spin" size={18} />
              正在加载案例材料…
            </div>
          </div>
        ) : loadError ? (
          <div className="workbench-state">
            <div className="state-box error-block">
              <AlertCircle size={18} />
              {loadError}
              <button className="btn btn-sm" onClick={() => setAttempt((value) => value + 1)}>
                重新加载
              </button>
            </div>
          </div>
        ) : caseData ? (
          <>
            <div className="case-strip">
              <div className="case-strip-main">
                <h1>{caseData.title}</h1>
                <div className="case-meta-line">
                  <span className="badge badge-platform">
                    {platformLabel(caseData.platform)}
                  </span>
                  <span className="badge badge-scope">
                    {scopeLabel(caseData.validation_scope)}
                  </span>
                  <span>{caseData.department || '科室未标注'}</span>
                  <span>版本 v{caseData.version_no}</span>
                  <span className="muted">案例 {caseData.id}</span>
                </div>
              </div>
              <div className="case-strip-note">
                <FileText size={14} />
                草稿为独立副本，原始病例材料保持只读
              </div>
            </div>

            <div className="columns">
              <section className="pane pane-left">
                <div className="pane-head pane-head-stack">
                  <div className="pane-title-row">
                    <h2>患者信息与原文</h2>
                    {replay ? (
                      <span className="status-chip status-ok">焦点范围已加载</span>
                    ) : (
                      <span className="status-chip status-idle">未加载回放范围</span>
                    )}
                  </div>
                  <div className="segmented">
                    <button
                      className={mode === 'scope' ? 'active' : ''}
                      onClick={() => setMode('scope')}
                    >
                      焦点可见范围
                    </button>
                    <button
                      className={mode === 'full' ? 'active' : ''}
                      onClick={() => setMode('full')}
                    >
                      完整病例材料
                    </button>
                  </div>
                </div>
                <p className="pane-hint">
                  {mode === 'scope' && replay
                    ? '左栏仅显示焦点发出时模型可看到的材料，与回放接口输出一致。'
                    : '按来源时间展示全部消息；范围外消息会标记排除原因。'}
                </p>
                <div className="pane-body">
                  {mode === 'scope' && !replay && (
                    <div className="inline-note">
                      尚未加载回放范围，当前显示完整病例材料。
                    </div>
                  )}
                  <div className="message-list">
                    {renderMessages.length === 0 ? (
                      <div className="inline-empty">该视图中没有可显示的消息</div>
                    ) : (
                      renderMessages.map(messageCard)
                    )}
                  </div>
                  {mode === 'scope' && replay && replay.excluded_message_ids.length > 0 && (
                    <section className="outside-block">
                      <h4>范围外 · 模型不可见（{replay.excluded_message_ids.length}）</h4>
                      {replay.excluded_message_ids.map((id) => {
                        const message = messageById.get(id);
                        if (!message) return null;
                        return (
                          <button
                            key={id}
                            className="outside-row"
                            onClick={() => locateMessage(id)}
                          >
                            <div className="outside-row-title">
                              <span
                                className={`role-mini role-mini-${message.role}`}
                              >
                                {message.role === 'patient' ? '患者' : '医生'}
                              </span>
                              <strong>
                                {message.speaker_name || '未署名'}
                              </strong>
                              <span className="meta-text">
                                {formatTime(message.created_at)}
                              </span>
                            </div>
                            <div className="outside-reason">
                              {replay.exclusion_reasons[id]}
                            </div>
                            <div className="outside-text">{message.text}</div>
                          </button>
                        );
                      })}
                    </section>
                  )}
                </div>
              </section>

              <section className="pane pane-center">
                <div className="pane-head">
                  <h2>已有观点与草稿</h2>
                  {doctorOpinions ? (
                    <span className="status-chip status-ok">观点已整理</span>
                  ) : opinionsBusy ? (
                    <span className="status-chip status-idle">整理中</span>
                  ) : opinionsError ? (
                    <span className="status-chip status-error">分析失败</span>
                  ) : (
                    <span className="status-chip status-idle">未分析</span>
                  )}
                </div>
                <div className="pane-body">
                  <section className="block">
                    <div className="block-head">
                      <h3>焦点回答</h3>
                      <span className="badge badge-neutral">回放截止点</span>
                    </div>
                    <select
                      className="select"
                      value={focusId}
                      onChange={(event) => handleFocusChange(event.target.value)}
                    >
                      {doctorMessages.length === 0 && (
                        <option value="">该案例没有医生回答</option>
                      )}
                      {doctorMessages.map((message) => (
                        <option key={message.id} value={message.id}>
                          {message.speaker_name || '未署名医生'} ·{' '}
                          {formatTime(message.created_at)} · {message.id}
                        </option>
                      ))}
                    </select>
                    <div className="review-context-switch">
                      <span className="context-label">审阅材料</span>
                      <div className="seg-control" aria-label="审阅材料范围">
                        <button
                          type="button"
                          className={`seg-button${
                            reviewContext === 'replay' ? ' seg-active' : ''
                          }`}
                          onClick={() => void switchReviewContext('replay')}
                        >
                          严格回放
                        </button>
                        <button
                          type="button"
                          className={`seg-button${
                            reviewContext === 'current' ? ' seg-active' : ''
                          }`}
                          onClick={() => void switchReviewContext('current')}
                        >
                          当前版本
                        </button>
                      </div>
                    </div>
                    <p className="context-hint">
                      严格回放只使用焦点时点前材料；当前版本额外包含已保存的人工补录。
                    </p>
                  </section>

                  <section className="block">
                    <div className="block-head">
                      <h3>前序回答</h3>
                      {replay && (
                        <span className="badge badge-muted">
                          {replay.prior_answer_message_ids.length} 条
                        </span>
                      )}
                    </div>
                    {replayBusy ? (
                      <div className="inline-state">
                        <Loader2 className="spin" size={14} />
                        正在加载严格前序范围…
                      </div>
                    ) : replay ? (
                      replay.prior_answer_message_ids.length === 0 ? (
                        <div className="inline-empty">该焦点之前没有可见医生回答</div>
                      ) : (
                        <div className="prior-list">
                          {replay.prior_answer_message_ids.map((id) => {
                            const message = messageById.get(id);
                            if (!message) return null;
                            return (
                              <button
                                key={id}
                                className="prior-item"
                                onClick={() => locateMessage(id)}
                              >
                                <div className="prior-head">
                                  <strong>
                                    {message.speaker_name || '未署名医生'}
                                  </strong>
                                  {message.speaker_title && (
                                    <span className="meta-text">
                                      {message.speaker_title}
                                    </span>
                                  )}
                                  {message.hospital && (
                                    <span className="meta-text">{message.hospital}</span>
                                  )}
                                </div>
                                <div className="prior-time">
                                  <Clock3 size={12} />
                                  {formatTime(message.created_at)}
                                </div>
                                <div className="prior-text">{message.text}</div>
                                <div className="prior-open">
                                  <ArrowUpRight size={13} />
                                  点击定位原文
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      )
                    ) : replayError ? (
                      <div className="inline-error">{replayError}</div>
                    ) : (
                      <div className="inline-empty">选择焦点回答后显示前序医生回答</div>
                    )}
                  </section>

                  <section className="block opinions-block">
                    <div className="block-head">
                      <h3>同行观点整理（焦点前）</h3>
                      <span className="badge badge-muted">
                        {doctorOpinions?.opinions.length ?? 0} 条
                      </span>
                    </div>
                    {opinionsBusy ? (
                      <div className="inline-state">
                        <Loader2 className="spin" size={14} />
                        正在整理前序医生观点…
                      </div>
                    ) : opinionsError ? (
                      <div className="inline-error">
                        <AlertCircle size={14} />
                        {opinionsError}
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            if (replay) {
                              void loadDoctorOpinions(
                                replay.focus_message_id,
                                replay.review_context,
                              );
                            }
                          }}
                        >
                          重新分析
                        </button>
                      </div>
                    ) : doctorOpinions ? (
                      doctorOpinions.opinions.length === 0 ? (
                        <div className="inline-empty">
                          焦点之前没有可整理的医生观点
                        </div>
                      ) : (
                        <div className="opinion-list">
                          {doctorOpinions.opinions.map(opinionCard)}
                        </div>
                      )
                    ) : (
                      <div className="inline-empty">
                        选择焦点回答后自动整理全部前序医生观点
                      </div>
                    )}
                  </section>

                  <section className="block draft-block">
                    <div className="block-head">
                      <h3>待审草稿</h3>
                      {caseData.draft && !draftEditing && (
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            setDraftText(caseData.draft?.current_text ?? '');
                            setDraftReason('');
                            setDraftError('');
                            setDraftEditing(true);
                          }}
                        >
                          <Pencil size={12} />
                          修改草稿
                        </button>
                      )}
                    </div>
                    {caseData.draft ? (
                      draftEditing ? (
                        <div className="draft-edit-box">
                          <textarea
                            className="draft-editor"
                            value={draftText}
                            disabled={draftSaving}
                            onChange={(event) => setDraftText(event.target.value)}
                          />
                          <input
                            className="field-input"
                            value={draftReason}
                            disabled={draftSaving}
                            placeholder="修改原因（可选，例如：补充剂量条件）"
                            onChange={(event) => setDraftReason(event.target.value)}
                          />
                          {draftError && (
                            <div className="inline-error">
                              <AlertCircle size={14} />
                              {draftError}
                            </div>
                          )}
                          <div className="inline-actions">
                            <button
                              className="btn btn-sm btn-primary"
                              disabled={draftSaving || !draftText.trim()}
                              onClick={() => void saveDraft()}
                            >
                              {draftSaving ? (
                                <Loader2 className="spin" size={12} />
                              ) : (
                                <Save size={12} />
                              )}
                              保存并重新分析
                            </button>
                            <button
                              className="btn btn-sm"
                              disabled={draftSaving}
                              onClick={() => setDraftEditing(false)}
                            >
                              取消
                            </button>
                            <span className="draft-hint">
                              保存后生成新草稿版本，上一版本保留
                            </span>
                          </div>
                        </div>
                      ) : (
                        <>
                          {jointReview ? (
                            <div className="draft-sentence-list">
                              {jointReview.draft_sentences.map(
                                (sentence, index) => (
                                  <div
                                    key={sentence.id}
                                    ref={(element) => {
                                      draftRefs.current[sentence.id] = element;
                                    }}
                                    className={`draft-sentence-item${
                                      highlightId === `draft:${sentence.id}`
                                        ? ' is-highlighted'
                                        : ''
                                    }`}
                                  >
                                    <span className="draft-sentence-no">
                                      {index + 1}
                                    </span>
                                    <span className="draft-sentence-text">
                                      {sentence.text}
                                    </span>
                                  </div>
                                ),
                              )}
                            </div>
                          ) : (
                            <div className="draft-text">
                              {caseData.draft.current_text}
                            </div>
                          )}
                          <div className="draft-meta">
                            <FileText size={13} />
                            版本 v{caseData.draft.version_no} · 更新于{' '}
                            {formatTime(caseData.draft.updated_at)}
                          </div>
                        </>
                      )
                    ) : (
                      <div className="inline-empty">该案例暂无独立草稿</div>
                    )}
                  </section>

                  <section className="block supplement-block">
                    <div className="block-head">
                      <h3>人工补录患者信息</h3>
                      <button
                        className="btn btn-sm"
                        onClick={() => setSupplementOpen((open) => !open)}
                      >
                        {supplementOpen ? (
                          <XCircle size={12} />
                        ) : (
                          <UserPlus size={12} />
                        )}
                        {supplementOpen ? '收起' : '补录'}
                      </button>
                    </div>
                    {supplementOpen ? (
                      <div className="supplement-form">
                        <p className="form-hint">
                          补录会创建新的病例版本；补录消息晚于焦点，不会进入既往焦点上下文的严格前序。
                        </p>
                        <textarea
                          className="supplement-textarea"
                          value={supplementText}
                          disabled={supplementSaving}
                          placeholder="患者补充内容（保持患者角色与原话）"
                          onChange={(event) => setSupplementText(event.target.value)}
                        />
                        <select
                          className="select"
                          value={supplementFindingId}
                          disabled={supplementSaving}
                          onChange={(event) =>
                            setSupplementFindingId(event.target.value)
                          }
                        >
                          <option value="">不关联审阅提示</option>
                          {jointReview?.findings.map((finding) => (
                            <option key={finding.id} value={finding.id}>
                              {finding.id.slice(0, 8)} ·{' '}
                              {finding.draft_sentence_text.slice(0, 48)}
                            </option>
                          ))}
                        </select>
                        <input
                          className="field-input"
                          value={supplementNote}
                          disabled={supplementSaving}
                          placeholder="补录说明（可选）"
                          onChange={(event) => setSupplementNote(event.target.value)}
                        />
                        {supplementError && (
                          <div className="inline-error">
                            <AlertCircle size={14} />
                            {supplementError}
                          </div>
                        )}
                        <div className="inline-actions">
                          <button
                            className="btn btn-sm btn-primary"
                            disabled={supplementSaving || !supplementText.trim()}
                            onClick={() => void saveSupplement()}
                          >
                            {supplementSaving ? (
                              <Loader2 className="spin" size={12} />
                            ) : (
                              <Save size={12} />
                            )}
                            保存并重新分析
                          </button>
                          <button
                            className="btn btn-sm"
                            disabled={supplementSaving}
                            onClick={() => {
                              setSupplementOpen(false);
                              setSupplementText('');
                              setSupplementFindingId('');
                              setSupplementNote('');
                              setSupplementError('');
                            }}
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="inline-empty">
                        补录患者补充的信息；保存后生成新病例版本，旧版本完整保留。
                      </div>
                    )}
                  </section>
                </div>
              </section>

              <section className="pane pane-right">
                <div className="pane-head">
                  <h2>联合审阅提示</h2>
                  {reviewDone ? (
                    <span className="status-chip status-ok">联合审阅已生成</span>
                  ) : reviewBusy ? (
                    <span className="status-chip status-idle">分析中</span>
                  ) : reviewFailed ? (
                    <span className="status-chip status-error">分析失败</span>
                  ) : (
                    <span className="status-chip status-idle">尚未分析</span>
                  )}
                </div>
                <div className="pane-body">
                  {!replay ? (
                    <div className="review-empty">
                      <ScanSearch size={26} />
                      <h3>等待焦点回答</h3>
                      <p>选择焦点回答并加载严格前序范围后开始分析。</p>
                      <div className="review-empty-foot">
                        不会显示未接入模块的推测结果。
                      </div>
                    </div>
                  ) : (
                    <>
                      <section className="block joint-block">
                        <div className="block-head">
                          <h3>同一主张联合审阅</h3>
                          <div className="inline-actions">
                            <span className="badge badge-muted">
                              {jointReview
                                ? `${jointReview.findings.length} 条`
                                : '未分析'}
                            </span>
                            {jointReview && (
                              <>
                                <button
                                  className="btn btn-sm"
                                  disabled={Boolean(exportSaving)}
                                  onClick={() => void downloadExport('json')}
                                >
                                  <Download size={12} />
                                  下载 JSON
                                </button>
                                <button
                                  className="btn btn-sm"
                                  disabled={Boolean(exportSaving)}
                                  onClick={() => void downloadExport('markdown')}
                                >
                                  <Download size={12} />
                                  下载 Markdown
                                </button>
                              </>
                            )}
                            {replay && (
                              <button
                                className="btn btn-sm"
                                disabled={jointBusy}
                                onClick={() =>
                                  void loadJointReview(
                                    replay.focus_message_id,
                                    replay.review_context,
                                  )
                                }
                              >
                                {jointBusy ? (
                                  <Loader2 className="spin" size={12} />
                                ) : (
                                  <RefreshCw size={12} />
                                )}
                                重新分析
                              </button>
                            )}
                          </div>
                        </div>
                        {jointBusy ? (
                          <div className="inline-state">
                            <Loader2 className="spin" size={14} />
                            正在对草稿逐句联合审阅…
                          </div>
                        ) : jointError ? (
                          <div className="inline-error">
                            <AlertCircle size={14} />
                            {jointError}
                            <button
                              className="btn btn-sm"
                              onClick={() => {
                                if (replay) {
                                  void loadJointReview(
                                    replay.focus_message_id,
                                    replay.review_context,
                                  );
                                }
                              }}
                            >
                              重新分析
                            </button>
                          </div>
                        ) : jointReview ? (
                          <>
                            <div className="facts-meta">
                              <Clock3 size={12} />
                              截止 {formatTime(jointReview.cutoff_at)}
                              <span className="meta-text">
                                {jointReview.source_mode === 'stub_rules'
                                  ? '规则桩'
                                  : jointReview.source_mode}{' '}
                                · {jointReview.model_name} ·{' '}
                                {jointReview.rules_version} · 运行{' '}
                                {jointReview.run_id.slice(0, 8)}
                              </span>
                            </div>
                            {exportError && (
                              <div className="inline-error">
                                <AlertCircle size={14} />
                                {exportError}
                              </div>
                            )}
                            {jointReview.previous_run_id && (
                              <div className="diff-block">
                                <h4>与上一审阅的提示变化</h4>
                                <div className="diff-reason">
                                  {diffReasonLabel(jointReview.diff_reason)}
                                </div>
                                {jointReview.diff.length === 0 ? (
                                  <div className="inline-empty">
                                    这次重跑未新增或解决提示，状态保持一致。
                                  </div>
                                ) : (
                                  <div className="diff-list">
                                    {jointReview.diff.map((item) => (
                                      <div
                                        key={item.signature}
                                        className={`diff-item diff-${item.change}`}
                                      >
                                        <span className="diff-change">
                                          {diffChangeLabel(item.change)}
                                        </span>
                                        <span className="diff-sentence">
                                          {item.draft_sentence_text}
                                        </span>
                                        <div className="diff-statuses">
                                          {item.old_status && (
                                            <span>
                                              原 {reviewStatusLabel(item.old_status)}
                                            </span>
                                          )}
                                          <span>
                                            现 {reviewStatusLabel(item.new_status)}
                                          </span>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                            {jointReview.findings.length === 0 ? (
                              <div className="inline-empty">
                                草稿没有可拆分审阅的语句
                              </div>
                            ) : (
                              <div className="joint-list">
                                {jointReview.findings.map(findingCard)}
                              </div>
                            )}
                            <p className="fact-scope-note">
                              每条同时标注“观点贡献”与“患者依据”；引用只来自焦点时点前可见材料，患者未提及不代表明确否认。
                            </p>
                          </>
                        ) : (
                          <div className="review-empty">
                            <ScanSearch size={26} />
                            <h3>尚未分析</h3>
                            <p>
                              选择焦点回答后将同一份草稿与患者事实、前序观点联合审阅。
                            </p>
                            <div className="review-empty-foot">
                              不会显示未完成校验的推测结果。
                            </div>
                          </div>
                        )}
                      </section>

                      <section className="block facts-block">
                        <div className="block-head">
                          <h3>患者依据</h3>
                          <span className="badge badge-muted">
                            {patientFacts
                              ? `${patientFacts.facts.length} 项`
                              : '未分析'}
                          </span>
                        </div>
                        {factsBusy ? (
                          <div className="inline-state">
                            <Loader2 className="spin" size={14} />
                            正在整理患者事实…
                          </div>
                        ) : factsError ? (
                          <div className="inline-error">
                            <AlertCircle size={14} />
                            {factsError}
                            <button
                              className="btn btn-sm"
                              onClick={() => {
                                if (replay) {
                                  void loadPatientFacts(
                                    replay.focus_message_id,
                                    replay.review_context,
                                  );
                                }
                              }}
                            >
                              重新分析
                            </button>
                          </div>
                        ) : patientFacts ? (
                          <>
                            <div className="facts-meta">
                              <Clock3 size={12} />
                              截止 {formatTime(patientFacts.cutoff_at)}
                              <span className="meta-text">
                                {patientFacts.model_name} ·{' '}
                                {patientFacts.rules_version} · 运行{' '}
                                {patientFacts.run_id.slice(0, 8)}
                              </span>
                            </div>
                            {patientFacts.facts.length === 0 ? (
                              <div className="inline-empty">
                                范围内没有可提取的患者事实
                              </div>
                            ) : (
                              <div className="fact-list">
                                {patientFacts.facts.map((fact) => (
                                  <article
                                    key={fact.id}
                                    className={`fact-item fact-${fact.status}`}
                                  >
                                    <div className="fact-top">
                                      <span
                                        className={`fact-status fact-status-${fact.status}`}
                                      >
                                        {patientFactStatusLabel(fact.status)}
                                      </span>
                                      <span className="badge badge-neutral">
                                        {patientFactDimensionLabel(fact.dimension)}
                                      </span>
                                      {fact.time_range && (
                                        <span className="fact-time">
                                          <Clock3 size={11} />
                                          {fact.time_range}
                                        </span>
                                      )}
                                    </div>
                                    <p className="fact-statement">{fact.statement}</p>
                                    {fact.condition && (
                                      <p className="fact-condition">
                                        {fact.condition}
                                      </p>
                                    )}
                                    {evidencePills(fact.evidence_refs)}
                                  </article>
                                ))}
                              </div>
                            )}
                            <p className="fact-scope-note">
                              患者事实只从焦点时点前可见材料提取；医生提问标为“已问未答”，不自动转成患者事实。
                            </p>
                          </>
                        ) : (
                          <div className="review-empty">
                            <ScanSearch size={26} />
                            <h3>尚未分析</h3>
                            <p>选择焦点回答后将自动整理范围可见的患者事实。</p>
                            <div className="review-empty-foot">
                              不会显示未接入模块的推测结果。
                            </div>
                          </div>
                        )}
                      </section>

                      <section className="block opinions-contrib-block">
                        <div className="block-head">
                          <h3>观点贡献 · 同行比较</h3>
                          <span className="badge badge-muted">
                            {doctorOpinions
                              ? `${doctorOpinions.peer_relations.length} 组`
                              : '未分析'}
                          </span>
                        </div>
                        {opinionsBusy ? (
                          <div className="inline-state">
                            <Loader2 className="spin" size={14} />
                            正在比较前序医生观点…
                          </div>
                        ) : opinionsError ? (
                          <div className="inline-error">
                            <AlertCircle size={14} />
                            {opinionsError}
                            <button
                              className="btn btn-sm"
                              onClick={() => {
                                if (replay) {
                                  void loadDoctorOpinions(
                                    replay.focus_message_id,
                                    replay.review_context,
                                  );
                                }
                              }}
                            >
                              重新分析
                            </button>
                          </div>
                        ) : doctorOpinions ? (
                          <>
                            <div className="facts-meta">
                              <Clock3 size={12} />
                              截止 {formatTime(doctorOpinions.cutoff_at)}
                              <span className="meta-text">
                                {doctorOpinions.model_name} ·{' '}
                                {doctorOpinions.rules_version} · 运行{' '}
                                {doctorOpinions.run_id.slice(0, 8)}
                              </span>
                            </div>
                            {doctorOpinions.peer_relations.length === 0 ? (
                              <div className="inline-empty">
                                前序观点之间没有检测到分歧或互补关系
                              </div>
                            ) : (
                              <div className="relation-list">
                                {doctorOpinions.peer_relations.map(relationCard)}
                              </div>
                            )}
                            <p className="fact-scope-note">
                              观点新增不等于正确，重复不等于无价值；比较保留时间、条件和不确定性，不依据职称或医院等级判定正确性。
                            </p>
                          </>
                        ) : (
                          <div className="review-empty">
                            <ScanSearch size={26} />
                            <h3>尚未分析</h3>
                            <p>选择焦点回答后将整理前序医生观点并与草稿联合审阅。</p>
                            <div className="review-empty-foot">
                              不会显示未接入模块的推测结果。
                            </div>
                          </div>
                        )}
                      </section>
                    </>
                  )}

                  <section className="block scope-block">
                    <div className="block-head">
                      <h3>严格前序覆盖范围</h3>
                      {replay && (
                        <span
                          className={`badge badge-quality badge-quality-${replay.order_quality}`}
                        >
                          {orderQualityLabel(replay.order_quality)}
                        </span>
                      )}
                      {replay && (
                        <span className="badge badge-neutral">
                          {replay.review_context === 'current'
                            ? '当前版本'
                            : '严格回放'}
                        </span>
                      )}
                    </div>
                    {replayBusy ? (
                      <div className="inline-state">
                        <Loader2 className="spin" size={14} />
                        正在计算回放范围…
                      </div>
                    ) : replayError ? (
                      <div className="inline-error">{replayError}</div>
                    ) : replay ? (
                      <>
                        <div className="scope-stats">
                          <div className="stat">
                            <span>
                              {
                                replay.visible_messages.filter(
                                  (message) => message.role === 'patient',
                                ).length
                              }
                            </span>
                            <label>可见患者材料</label>
                          </div>
                          <div className="stat">
                            <span>{replay.prior_answer_message_ids.length}</span>
                            <label>前序医生回答</label>
                          </div>
                          <div className="stat">
                            <span>{replay.excluded_message_ids.length}</span>
                            <label>范围外消息</label>
                          </div>
                        </div>
                        <div className="scope-meta">
                          <Clock3 size={13} />
                          截止 {formatTime(replay.cutoff_at)}
                          {replay.review_context === 'current' && (
                            <span className="meta-text">
                              已包含人工补录患者消息
                            </span>
                          )}
                        </div>

                        <div className="locator">
                          <div className="locator-title">证据定位</div>
                          <div className="locator-hint">点击条目标记并滚动到原文</div>
                          <div className="locator-group">
                            <span className="locator-label">患者材料</span>
                            {replay.patient_message_ids.length === 0 ? (
                              <span className="muted">无</span>
                            ) : (
                              replay.patient_message_ids.map((id) => {
                                const message = messageById.get(id);
                                if (!message) return null;
                                return (
                                  <button
                                    key={id}
                                    className="locator-chip"
                                    onClick={() => locateMessage(id)}
                                  >
                                    <UserRound size={12} />
                                    {message.speaker_name || '患者'} ·{' '}
                                    {formatShortTime(message.created_at)}
                                  </button>
                                );
                              })
                            )}
                          </div>
                          <div className="locator-group">
                            <span className="locator-label">前序医生回答</span>
                            {replay.prior_answer_message_ids.map((id) => {
                              const message = messageById.get(id);
                              if (!message) return null;
                              return (
                                <button
                                  key={id}
                                  className="locator-chip"
                                  onClick={() => locateMessage(id)}
                                >
                                  <MessageSquareText size={12} />
                                  {message.speaker_name || '未署名'} ·{' '}
                                  {formatShortTime(message.created_at)}
                                </button>
                              );
                            })}
                          </div>
                          <div className="locator-group locator-excluded">
                            <span className="locator-label">范围外</span>
                            {replay.excluded_message_ids.length === 0 ? (
                              <span className="muted">无</span>
                            ) : (
                              <>
                                {replay.excluded_message_ids.slice(0, 6).map((id) => {
                                  const message = messageById.get(id);
                                  if (!message) return null;
                                  return (
                                    <button
                                      key={id}
                                      className="locator-chip locator-excluded-chip"
                                      onClick={() => locateMessage(id)}
                                    >
                                      {message.speaker_name || '未署名'} ·{' '}
                                      {formatShortTime(message.created_at)}
                                    </button>
                                  );
                                })}
                                {replay.excluded_message_ids.length > 6 && (
                                  <span className="muted">
                                    等 {replay.excluded_message_ids.length} 条
                                  </span>
                                )}
                              </>
                            )}
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="inline-empty">选择焦点回答后显示回放范围</div>
                    )}
                  </section>
                </div>
              </section>
            </div>
          </>
        ) : null}
      </main>
    </>
  );
}
