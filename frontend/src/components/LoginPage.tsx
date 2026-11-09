import { FormEvent, useState } from 'react';
import { Lock, LogIn, ShieldCheck, Stethoscope } from 'lucide-react';

import { api, authStore, errorMessage } from '../api';
import type { Session } from '../types';

interface LoginPageProps {
  onLogin: (session: Session) => void;
}

export function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('demo');
  const [password, setPassword] = useState('demo1234');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const session = await api.login(username.trim(), password);
      authStore.setSession(session);
      onLogin(session);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-brand">
        <div className="login-logo">
          <Stethoscope size={28} />
        </div>
        <h1>医知协同</h1>
        <p className="login-en">MediPeer</p>
        <p className="login-desc">医生在线咨询回复的联合审阅工作台。</p>
      </div>

      <form className="login-card" onSubmit={submit}>
        <h2>登录工作台</h2>
        <div className="field">
          <label htmlFor="login-username">用户名</label>
          <input
            id="login-username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
          />
        </div>
        <div className="field">
          <label htmlFor="login-password">密码</label>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />
        </div>
        {error && (
          <div className="form-error">
            <Lock size={13} />
            {error}
          </div>
        )}
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
          <LogIn size={16} />
          {busy ? '登录中…' : '登录'}
        </button>
      </form>

      <div className="demo-accounts">
        <div className="demo-accounts-title">
          <ShieldCheck size={15} />
          评审演示账号（仅演示环境）
        </div>
        <div className="demo-account">demo / demo1234</div>
        <div className="demo-account">judge / judge1234</div>
      </div>
    </div>
  );
}
