import { useEffect, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  FolderOpen,
  Loader2,
  LogOut,
  Plus,
  Stethoscope,
} from 'lucide-react';

import { api, errorMessage } from '../api';
import { formatTime, platformLabel, scopeLabel } from '../labels';
import type { CaseSummary, DemoTemplate, UserView } from '../types';

interface CaseLibraryProps {
  user: UserView;
  onOpenCase: (caseId: string) => void;
  onLogout: () => void;
}

export function CaseLibrary({ user, onOpenCase, onLogout }: CaseLibraryProps) {
  const [templates, setTemplates] = useState<DemoTemplate[]>([]);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState<string | null>(null);
  const [createError, setCreateError] = useState('');

  async function refresh() {
    setLoading(true);
    setError('');
    try {
      const [templateItems, caseItems] = await Promise.all([
        api.listDemoTemplates(),
        api.listCases(),
      ]);
      setTemplates(templateItems);
      setCases(caseItems);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function createCase(templateKey: string) {
    setCreating(templateKey);
    setCreateError('');
    try {
      const created = await api.createDemoCase(templateKey);
      onOpenCase(created.id);
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(null);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand">
            <Stethoscope size={19} />
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

      <main className="library-main">
        <div className="page-head">
          <h1>案例库</h1>
          <p>
            加载脱敏咨询并进入审阅工作台。演示案例会复制到当前评审会话，与会话外的其他案例和评审修改隔离。
          </p>
        </div>

        <section className="panel">
          <div className="panel-head">
            <h2>演示模板</h2>
            <span className="muted">复制到当前会话后独立保存</span>
          </div>
          <div className="template-grid">
            {templates.map((template) => (
              <article className="template-item" key={template.key}>
                <div className="template-meta-row">
                  <span className="badge badge-platform">
                    {platformLabel(template.platform)}
                  </span>
                  <span className="badge badge-scope">
                    {scopeLabel(template.validation_scope)}
                  </span>
                </div>
                <h3>{template.title}</h3>
                <p>{template.department || '科室未标注'} · 含患者材料、医生回答与独立草稿</p>
                <div className="template-actions">
                  <button
                    className="btn btn-primary btn-sm"
                    disabled={creating === template.key}
                    onClick={() => void createCase(template.key)}
                  >
                    {creating === template.key ? (
                      <Loader2 className="spin" size={14} />
                    ) : (
                      <Plus size={14} />
                    )}
                    {creating === template.key ? '正在复制' : '复制到我的案例'}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>当前会话案例</h2>
            <span className="muted">仅显示当前账号可访问的案例</span>
          </div>
          {loading ? (
            <div className="empty-state">
              <Loader2 className="spin" size={20} />
              正在加载案例列表…
            </div>
          ) : error ? (
            <div className="empty-state error-block">
              <AlertCircle size={20} />
              {error}
              <button className="btn btn-sm" onClick={() => void refresh()}>
                重新加载
              </button>
            </div>
          ) : cases.length === 0 ? (
            <div className="empty-state">
              <FolderOpen size={22} />
              还没有案例
              <span className="muted">从上方演示模板复制一个案例后开始审阅。</span>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>案例</th>
                    <th>科室</th>
                    <th>来源</th>
                    <th>验证范围</th>
                    <th>版本</th>
                    <th>更新时间</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <div className="case-title-cell">
                          <span className="case-id">{item.id.slice(0, 8)}</span>
                          <span>{item.title}</span>
                        </div>
                      </td>
                      <td>{item.department || '—'}</td>
                      <td>{platformLabel(item.platform)}</td>
                      <td>
                        <span className="badge badge-scope">
                          {scopeLabel(item.validation_scope)}
                        </span>
                      </td>
                      <td>v{item.version_no}</td>
                      <td>{formatTime(item.updated_at)}</td>
                      <td>
                        <button
                          className="btn btn-primary btn-sm"
                          onClick={() => onOpenCase(item.id)}
                        >
                          打开工作台
                          <ArrowRight size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {createError && <div className="panel-foot-error">{createError}</div>}
        </section>
      </main>
    </div>
  );
}
