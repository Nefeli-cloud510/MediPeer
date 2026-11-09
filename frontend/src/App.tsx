import { useEffect, useState } from 'react';

import { api, authStore } from './api';
import { CaseLibrary } from './components/CaseLibrary';
import { LoginPage } from './components/LoginPage';
import { Workbench } from './components/Workbench';
import type { Session } from './types';

type Route = { view: 'cases' } | { view: 'case'; caseId: string };

function parseHash(): Route {
  const match = window.location.hash.match(/^#\/case\/([^/?#]+)/);
  if (match) {
    return { view: 'case', caseId: decodeURIComponent(match[1]) };
  }
  return { view: 'cases' };
}

export default function App() {
  const [session, setSession] = useState<Session | null>(() => authStore.getSession());
  const [route, setRoute] = useState<Route>(() => parseHash());
  const [restoring, setRestoring] = useState(true);
  const token = session?.token ?? null;

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    const onExpired = () => {
      setSession(null);
      setRoute({ view: 'cases' });
      window.location.hash = '#/cases';
    };
    window.addEventListener('hashchange', onHash);
    window.addEventListener('medipeer:auth-expired', onExpired);
    return () => {
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener('medipeer:auth-expired', onExpired);
    };
  }, []);

  useEffect(() => {
    if (!token) {
      setRestoring(false);
      return;
    }
    let cancelled = false;
    setRestoring(true);
    api
      .me()
      .then((user) => {
        if (cancelled) return;
        setSession((current: Session | null) => {
          if (!current) return current;
          const refreshed = authStore.setUser(user);
          return refreshed ?? current;
        });
      })
      .catch(() => {
        if (!cancelled) {
          authStore.clear();
          setSession(null);
          setRoute({ view: 'cases' });
        }
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (!session) {
    return (
      <LoginPage
        onLogin={(nextSession) => {
          setSession(nextSession);
          setRoute({ view: 'cases' });
          window.location.hash = '#/cases';
        }}
      />
    );
  }

  if (restoring) {
    return (
      <main className="full-screen-state">
        <div className="state-box">正在恢复会话与案例状态…</div>
      </main>
    );
  }

  const goToCases = () => {
    window.location.hash = '#/cases';
  };
  const logout = () => {
    authStore.clear();
    setSession(null);
    setRoute({ view: 'cases' });
    window.location.hash = '#/cases';
  };

  if (route.view === 'case') {
    return (
      <Workbench
        key={route.caseId}
        caseId={route.caseId}
        user={session.user}
        onBack={goToCases}
        onLogout={logout}
      />
    );
  }

  return (
    <CaseLibrary
      user={session.user}
      onOpenCase={(caseId) => {
        window.location.hash = `#/case/${encodeURIComponent(caseId)}`;
      }}
      onLogout={logout}
    />
  );
}
