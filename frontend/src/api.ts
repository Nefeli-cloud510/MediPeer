import type {
  CaseDetail,
  CaseExportView,
  CaseSummary,
  DemoTemplate,
  DoctorOpinionAnalysisResponse,
  DraftUpdateRequest,
  DraftView,
  JointReviewAnalysisResponse,
  LoginResponse,
  PatientFactAnalysisResponse,
  ReplayResponse,
  ReviewActionRequest,
  ReviewActionView,
  Session,
  SupplementRequest,
  SupplementView,
  UserView,
} from './types';

const TOKEN_KEY = 'medipeer.auth.token';
const USER_KEY = 'medipeer.auth.user';

export class ApiError extends Error {
  constructor(
    public status: number,
    detail: string,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

export function errorMessage(err: unknown): string {
  return err instanceof ApiError
    ? `请求失败（${err.status}）：${err.message}`
    : err instanceof Error
      ? err.message
      : '未知错误';
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(path, { ...init, headers });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    if (response.status === 401) {
      authStore.clear();
      window.dispatchEvent(new Event('medipeer:auth-expired'));
    }
    const detail =
      payload &&
      typeof payload === 'object' &&
      'detail' in payload &&
      payload.detail != null
        ? String(payload.detail)
        : text || response.statusText;
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export const api = {
  login(username: string, password: string) {
    return request<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
  },
  me() {
    return request<UserView>('/api/auth/me');
  },
  listDemoTemplates() {
    return request<DemoTemplate[]>('/api/demo/templates');
  },
  createDemoCase(templateKey: string) {
    return request<CaseDetail>(`/api/cases/demo/${encodeURIComponent(templateKey)}`, {
      method: 'POST',
    });
  },
  listCases() {
    return request<CaseSummary[]>('/api/cases');
  },
  getCase(caseId: string) {
    return request<CaseDetail>(`/api/cases/${encodeURIComponent(caseId)}`);
  },
  getDraft(caseId: string) {
    return request<DraftView>(`/api/cases/${encodeURIComponent(caseId)}/draft`);
  },
  exportCase(
    caseId: string,
    runId: string,
    format: 'json' | 'markdown',
  ) {
    return request<CaseExportView>(
      `/api/cases/${encodeURIComponent(caseId)}/exports?run_id=${encodeURIComponent(runId)}&format=${format}`,
    );
  },
  submitReviewAction(caseId: string, payload: ReviewActionRequest) {
    return request<ReviewActionView>(
      `/api/cases/${encodeURIComponent(caseId)}/review-actions`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  },
  addSupplement(caseId: string, payload: SupplementRequest) {
    return request<SupplementView>(
      `/api/cases/${encodeURIComponent(caseId)}/supplements`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  },
  updateDraft(caseId: string, payload: DraftUpdateRequest) {
    return request<DraftView>(`/api/cases/${encodeURIComponent(caseId)}/draft`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  },
  replay(
    caseId: string,
    focusMessageId: string,
    reviewContext: 'replay' | 'current' = 'replay',
  ) {
    return request<ReplayResponse>(
      `/api/cases/${encodeURIComponent(caseId)}/replay`,
      {
        method: 'POST',
        body: JSON.stringify({
          focus_message_id: focusMessageId,
          review_context: reviewContext,
        }),
      },
    );
  },
  analyzePatientFacts(
    caseId: string,
    focusMessageId: string,
    reviewContext: 'replay' | 'current' = 'replay',
  ) {
    return request<PatientFactAnalysisResponse>(
      `/api/cases/${encodeURIComponent(caseId)}/patient-facts`,
      {
        method: 'POST',
        body: JSON.stringify({
          focus_message_id: focusMessageId,
          review_context: reviewContext,
        }),
      },
    );
  },
  analyzeDoctorOpinions(
    caseId: string,
    focusMessageId: string,
    reviewContext: 'replay' | 'current' = 'replay',
  ) {
    return request<DoctorOpinionAnalysisResponse>(
      `/api/cases/${encodeURIComponent(caseId)}/doctor-opinions`,
      {
        method: 'POST',
        body: JSON.stringify({
          focus_message_id: focusMessageId,
          review_context: reviewContext,
        }),
      },
    );
  },
  analyzeJointReview(
    caseId: string,
    focusMessageId: string,
    reviewContext: 'replay' | 'current' = 'replay',
  ) {
    return request<JointReviewAnalysisResponse>(
      `/api/cases/${encodeURIComponent(caseId)}/joint-review`,
      {
        method: 'POST',
        body: JSON.stringify({
          focus_message_id: focusMessageId,
          review_context: reviewContext,
        }),
      },
    );
  },
};

export const authStore = {
  getSession(): Session | null {
    const token = localStorage.getItem(TOKEN_KEY);
    const rawUser = localStorage.getItem(USER_KEY);
    if (!token || !rawUser) return null;
    try {
      return { token, user: JSON.parse(rawUser) as UserView };
    } catch {
      this.clear();
      return null;
    }
  },
  setSession(session: Session) {
    localStorage.setItem(TOKEN_KEY, session.token);
    localStorage.setItem(USER_KEY, JSON.stringify(session.user));
  },
  setUser(user: UserView) {
    const existing = this.getSession();
    if (existing) {
      localStorage.setItem(USER_KEY, JSON.stringify(user));
      return { token: existing.token, user };
    }
    return null;
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
};
