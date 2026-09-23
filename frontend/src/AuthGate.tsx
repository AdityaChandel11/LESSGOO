import { useCallback, useEffect, useState } from "react";

import App from "./App";
import BrandMark from "./Brand";
import Landing from "./Landing";
import SignInPanel from "./SignInPanel";
import Workspace from "./workspace/Shell";
import {
  ROLE_LABEL,
  type Session,
  SESSION_EXPIRED_EVENT,
  auth,
} from "./api";

/**
 * The only two paths this app distinguishes. Everything the signed-in app
 * needs to remember about where you are lives in the query string (see
 * App.tsx), so the path is free to carry just this one decision: the public
 * landing page, or the sign-in form. The server serves index.html for any
 * path, so /sign-in survives a refresh and a pasted link.
 */
const LANDING_PATH = "/";
const SIGN_IN_PATH = "/sign-in";

function SignIn({
  onSignedIn,
  onBack,
}: {
  onSignedIn: (s: Session) => void;
  onBack?: () => void;
}) {
  return (
    <div className="flex min-h-full items-center justify-center bg-canvas px-5 py-12 font-sans text-ink">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex items-center gap-2.5">
          <BrandMark />
          <div className="text-[15px] font-semibold tracking-tight">SwasthSetu</div>
        </div>

        {onBack && (
          <button
            type="button"
            onClick={onBack}
            className="mb-6 rounded-sm text-[12.5px] font-medium text-ink-3 hover:text-ink focus:ring-2 focus:ring-brand/30 focus:outline-none"
          >
            ← Back to the overview
          </button>
        )}

        <SignInPanel onSignedIn={onSignedIn} />
      </div>
    </div>
  );
}

export default function AuthGate() {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [expired, setExpired] = useState(false);
  const [path, setPath] = useState(() => window.location.pathname);
  // Whether this visit has gone through the front door yet. A session cookie
  // outlives the tab, so without this the deployed link opens straight into
  // whoever was last signed in on that browser — which is the one thing the
  // front door exists to prevent. It is not a sign-out: the session is intact
  // and one click resumes it.
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    auth
      .me()
      .then(setSession)
      .catch(() => setSession(null))
      .finally(() => setChecking(false));
  }, []);

  // The back button has to work on a page a stranger reached from a search
  // result, so the two public views are real history entries rather than state.
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = useCallback((next: string) => {
    window.history.pushState(null, "", next);
    setPath(next);
  }, []);

  const signedIn = useCallback((s: Session) => {
    setExpired(false);
    // The app keeps its own place in the query string; /sign-in is not a page
    // it has, so leaving it in the address bar would only confuse a refresh.
    window.history.replaceState(null, "", LANDING_PATH);
    setPath(LANDING_PATH);
    setEntered(true);
    setSession(s);
  }, []);

  useEffect(() => {
    const onExpired = () => {
      setSession(null);
      setExpired(true);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await auth.logout();
    } finally {
      setSession(null);
      setExpired(false);
      setEntered(false);
      window.history.replaceState(null, "", LANDING_PATH);
      setPath(LANDING_PATH);
    }
  }, []);

  if (checking) {
    return (
      <div className="flex h-full items-center justify-center bg-canvas" aria-busy="true">
        <BrandMark size={36} />
      </div>
    );
  }

  if (!session) {
    // A session that ended mid-task goes straight to the form; sending someone
    // back to the front door to start reading again would be a small insult.
    if (path !== SIGN_IN_PATH && !expired) {
      return <Landing onSignedIn={signedIn} />;
    }
    return (
      <>
        {expired && (
          <div role="status" className="fixed inset-x-0 top-0 z-50 bg-risk px-4 py-2 text-center text-[13px] font-medium text-white">
            Your session ended. Please sign in again.
          </div>
        )}
        <SignIn
          onSignedIn={signedIn}
          onBack={expired ? undefined : () => go(LANDING_PATH)}
        />
      </>
    );
  }

  // A session that survived in a cookie still arrives at the front door. The
  // deployed link is the first thing a judge or an officer sees, and it has to
  // open on the page that explains the system rather than inside the console
  // of whoever used this browser last.
  if (!entered && path === LANDING_PATH) {
    return (
      <Landing
        onSignedIn={signedIn}
        resume={{
          label: `${session.user.name} · ${ROLE_LABEL[session.user.role]}`,
          onResume: () => setEntered(true),
        }}
      />
    );
  }

  // Facility staff get the workspace for their own centre, not the national
  // console. The two are different shapes for different desks: App is a
  // fixed two-column map console, this is a phone app for one shelf.
  if (session.user.role === "facility_user" && session.user.facility_id) {
    return <Workspace session={session} onSignOut={signOut} />;
  }

  return <App session={session} onSignOut={signOut} />;
}
