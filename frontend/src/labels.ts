export function scopeLabel(scope: string): string {
  switch (scope) {
    case 'respiratory_adult_zh':
      return '成人呼吸道·中文';
    case 'generic_text':
      return '通用文本';
    default:
      return scope || '未标注';
  }
}

export function platformLabel(platform: string): string {
  return platform === 'haodafu' ? '好大夫' : '微医';
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '时间未知';
  return value.replace('T', ' ').replace('Z', ' UTC');
}

export function formatShortTime(value: string | null | undefined): string {
  const full = formatTime(value);
  return full === '时间未知' ? '时间未知' : full.slice(0, 16);
}

export function orderQualityLabel(quality: string): string {
  switch (quality) {
    case 'ok':
      return '时间可信';
    case 'partial':
      return '部分时间待核查';
    case 'unknown':
      return '顺序无法确认';
    default:
      return quality;
  }
}

export function patientFactStatusLabel(status: string): string {
  switch (status) {
    case 'confirmed':
      return '患者陈述';
    case 'denied':
      return '明确否认';
    case 'uncertain':
      return '不确定表述';
    case 'unknown':
      return '患者未提及';
    case 'asked_unanswered':
      return '已问未答';
    default:
      return status;
  }
}

export function patientFactDimensionLabel(dimension: string): string {
  switch (dimension) {
    case '人口学信息':
      return '人口学';
    case '诱因与情境':
      return '诱因情境';
    case '临床时间信息':
      return '时间信息';
    case '症状特征':
      return '症状特征';
    case '信息连续性':
      return '连续性';
    default:
      return dimension;
  }
}

export function opinionClaimTypeLabel(claimType: string): string {
  switch (claimType) {
    case 'clarifying_question':
      return '医生提问';
    case 'recommendation':
      return '建议';
    case 'general_knowledge':
      return '一般医学知识';
    case 'patient_fact_statement':
      return '患者事实';
    case 'possible_explanation':
      return '推测解释';
    default:
      return claimType;
  }
}

export function opinionCertaintyLabel(certainty: string): string {
  switch (certainty) {
    case 'asserted':
      return '明确表述';
    case 'conditional':
      return '条件性';
    case 'hedged':
      return '推测/留有余地';
    default:
      return certainty;
  }
}

export function opinionPolarityLabel(polarity: string): string {
  return polarity === 'negative' ? '否定/限制' : '肯定';
}

export function opinionDraftRelationLabel(relation: string): string {
  switch (relation) {
    case 'answered_by_draft':
      return '草稿已回应';
    case 'unanswered_question':
      return '草稿未回应';
    case 'diverging':
      return '与草稿分歧';
    case 'conditional_only':
      return '仅限条件';
    case 'complementing':
      return '补充内容';
    case 'reinforcing':
      return '方向一致';
    default:
      return relation;
  }
}

export function peerRelationTypeLabel(relationType: string): string {
  switch (relationType) {
    case 'repeat':
      return '重复观点';
    case 'supplement':
      return '互补观点';
    case 'divergence':
      return '观点分歧';
    case 'conditional_diff':
      return '适用条件不同';
    case 'unresolved':
      return '待核查';
    default:
      return relationType;
  }
}

export function peerRelationKindLabel(kind: string | null): string {
  switch (kind) {
    case 'polarity':
      return '方向不同';
    case 'condition':
      return '条件不同';
    case 'scope':
      return '角度互补';
    case null:
      return '';
    default:
      return kind ?? '';
  }
}

export function jointContributionLabel(contribution: string): string {
  switch (contribution) {
    case 'new_claim':
      return '草稿新增';
    case 'repeats_prior':
      return '重复前序';
    case 'reinforces_prior':
      return '呼应前序';
    case 'answers_prior_question':
      return '回应提问';
    case 'unanswered_question':
      return '未回应提问';
    case 'diverges_from_prior':
      return '与前序分歧';
    case 'mixed_prior_views':
      return '前序观点混合';
    case 'condition_only':
      return '仅限适用条件';
    default:
      return contribution;
  }
}

export function jointGroundingLabel(grounding: string): string {
  switch (grounding) {
    case 'patient_stated':
      return '患者已陈述';
    case 'patient_denied':
      return '患者明确否认';
    case 'patient_uncertain':
      return '患者表述不确定';
    case 'unmentioned':
      return '患者未提及';
    case 'detail_unverified':
      return '细节缺少来源';
    case 'doctor_question_only':
      return '仅有医生提问';
    case 'mixed_temporal':
      return '时间混合待核';
    case 'professional_recommendation':
      return '医学建议';
    case 'needs_review':
      return '来源待核查';
    default:
      return grounding;
  }
}

export function jointActionLabel(action: string): string {
  switch (action) {
    case 'keep_and_confirm':
      return '保留并确认';
    case 'defer':
      return '暂缓下结论';
    case 'revise_fact_statement':
      return '修改事实表述';
    case 'clarify_or_revise':
      return '澄清或修订';
    case 'clarify_condition':
      return '澄清条件';
    case 'view_both_sources':
      return '核对双方来源';
    default:
      return action;
  }
}

export function jointEvidenceKindLabel(kind: string): string {
  switch (kind) {
    case 'patient_statement':
      return '患者陈述';
    case 'doctor_question_inference':
      return '医生提问推定';
    case 'doctor_opinion':
      return '医生观点';
    case 'doctor_question':
      return '医生提问';
    case 'doctor_condition':
      return '适用条件';
    default:
      return kind;
  }
}

export function reviewStatusLabel(status: string): string {
  switch (status) {
    case 'pending':
      return '待核实';
    case 'confirmed':
      return '已确认';
    case 'rejected':
      return '已驳回';
    case 'question_adopted':
      return '补问待答';
    default:
      return status;
  }
}

export function diffChangeLabel(change: string): string {
  switch (change) {
    case 'added':
      return '新增提示';
    case 'resolved':
      return '已解决';
    case 'unchanged':
      return '保持不变';
    default:
      return change;
  }
}

export function diffReasonLabel(reason: string | null): string {
  switch (reason) {
    case 'case_version_changed':
      return '患者信息版本更新后重新分析';
    case 'draft_version_changed':
      return '草稿版本更新后重新分析';
    case 'input_changed':
      return '患者信息与草稿均更新后重新分析';
    default:
      return '对照当前结果与原审阅结果';
  }
}
