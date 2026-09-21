import { type FormEvent, useCallback, useEffect, useState } from "react";

import App from "./App";
import DataNotice from "./DataNotice";
import {
  ApiError,
  type DemoAccount,
  ROLE_LABEL,
  type Session,
  SESSION_EXPIRED_EVENT,
  auth,
} from "./api";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" aria-hidden="true">
      <rect width="26" height="26" rx="6" fill="#0b3d5c" />
      <path d="M13 6v14M6 13h14" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      <circle cx="19.5" cy="6.5" r="3" fill="#e0900e" stroke="#0b3d5c" strokeWidth="1.5" />
    </svg>
  );
}

function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState<DemoAccount[]>([]);
  const [demoBusy, setDemoBusy] = useState<string | null>(null);

  useEffect(() => {
    // 404 outside demo mode, which simply means there is no demo section.
    auth.demoAccounts().then(setDemoAccounts).catch(() => setDemoAccounts([]));
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = email.trim();
    if (!EMAIL_PATTERN.test(trimmed)) {
      setEmailError("Enter the email address of your account, for example name@health.gov.in");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await auth.login(trimmed, password));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(
          demoAccounts.length
            ? "Email or password is incorrect. To explore the demo, use one of the Continue buttons below — no password needed."
            : "Email or password is incorrect. Passwords are case-sensitive.",
        );
      } else {
        setError(err instanceof ApiError ? err.message : "Sign-in failed. Please try again.");
      }
      setBusy(false);
    }
  };

  const enterDemo = async (account: DemoAccount) => {
    setDemoBusy(account.email);
    setError(null);
    try {
      onSignedIn(await auth.demoLogin(account.email));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open the demo account.");
      setDemoBusy(null);
    }
  };

  return (
    <div className="flex min-h-full bg-canvas font-sans text-ink">
      <div className="relative hidden w-[46%] flex-col justify-between overflow-hidden bg-brand p-10 text-white lg:flex">
        <div className="flex items-center gap-3">
          <BrandMark size={34} />
          <div>
            <div className="text-[17px] font-semibold tracking-tight">SwasthSetu</div>
            <div className="text-[12px] text-white/70">National Health Supply Command</div>
          </div>
        </div>

        <div className="max-w-md">
          <h1 className="text-[30px] leading-tight font-semibold tracking-tight">
            Medicine on the shelf, before the patient arrives.
          </h1>
          <p className="mt-4 text-[14.5px] leading-relaxed text-white/75">
            Live stock across primary and community health centres, transfers planned before
            shelves empty, and reports that reach the network from any phone.
          </p>
          <dl className="mt-8 grid grid-cols-3 gap-4 border-t border-white/15 pt-6">
            {[
              ["State by state", "zoom from India to one facility"],
              ["Human approved", "no transfer moves on its own"],
              ["Any phone", "app, SMS or a voice call"],
            ].map(([t, d]) => (
              <div key={t}>
                <dt className="text-[13px] font-semibold">{t}</dt>
                <dd className="mt-1 text-[12px] leading-snug text-white/65">{d}</dd>
              </div>
            ))}
          </dl>
        </div>

        <p className="text-[11.5px] text-white/50">
          Authorised health department staff only. Activity is recorded.
        </p>
        <svg
          className="pointer-events-none absolute -right-24 -bottom-24 opacity-[0.08]"
          width="420"
          height="420"
          viewBox="0 0 420 420"
          aria-hidden="true"
        >
          {[200, 160, 120, 80].map((r) => (
            <circle key={r} cx="210" cy="210" r={r} fill="none" stroke="#fff" strokeWidth="18" />
          ))}
        </svg>
      </div>

      <main className="flex flex-1 items-center justify-center px-5 py-10">
        <div className="w-full max-w-[400px]">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <BrandMark />
            <div className="text-[15px] font-semibold tracking-tight">SwasthSetu</div>
          </div>

          {demoAccounts.length > 0 && (
            <section aria-labelledby="demo-heading" className="mb-8">
              <h2 id="demo-heading" className="text-[22px] font-semibold tracking-tight">
                Explore the demo
              </h2>
              <p className="mt-1 text-[13px] text-ink-2">
                Stock figures are simulated. Choose a role to see what that person can do — no
                password needed.
              </p>
              <ul className="mt-4 space-y-2">
                {demoAccounts.map((a) => (
                  <li key={a.email}>
                    <button
                      type="button"
                      onClick={() => enterDemo(a)}
                      disabled={demoBusy !== null}
                      className="flex w-full items-center justify-between gap-3 rounded-lg border border-line bg-panel px-3.5 py-2.5 text-left hover:border-brand/50 hover:bg-brand/[0.03] disabled:opacity-60"
                    >
                      <span className="min-w-0">
                        <span className="block text-[13.5px] font-medium text-ink">
                          {ROLE_LABEL[a.role]}
                        </span>
                        <span className="block truncate text-[12px] text-ink-3">
                          {a.name}
                          {a.role !== "admin" && a.role !== "facility_user" ? ` · ${a.scope}` : ""}
                        </span>
                      </span>
                      <span className="shrink-0 text-[12.5px] font-medium text-brand">
                        {demoBusy === a.email ? "Opening…" : "Continue →"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="mt-8 flex items-center gap-3 text-[11.5px] text-ink-3">
                <span className="h-px flex-1 bg-line" />
                or sign in with an account
                <span className="h-px flex-1 bg-line" />
              </div>
            </section>
          )}

          {demoAccounts.length === 0 && (
            <>
              <h2 className="text-[22px] font-semibold tracking-tight">Sign in</h2>
              <p className="mt-1 text-[13px] text-ink-2">
                Use the account issued by your department.
              </p>
            </>
          )}

          <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
            <div>
              <label htmlFor="email" className="block text-[12.5px] font-medium text-ink">
                Email address
              </label>
              <input
                id="email"
                type="email"
                autoComplete="username"
                inputMode="email"
                required
                value={email}
                aria-invalid={emailError ? true : undefined}
                aria-describedby={emailError ? "email-error" : undefined}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setEmailError(null);
                }}
                placeholder="name@health.gov.in"
                className="mt-1.5 h-10 w-full rounded-md border border-line bg-panel px-3 text-[14px] placeholder:text-ink-3/70 focus:border-brand focus:ring-2 focus:ring-brand/15 focus:outline-none aria-[invalid]:border-crit"
              />
              {emailError && (
                <p id="email-error" className="mt-1.5 text-[12px] text-crit">
                  {emailError}
                </p>
              )}
            </div>
            <div>
              <label htmlFor="password" className="block text-[12.5px] font-medium text-ink">
                Password
              </label>
              <div className="relative mt-1.5">
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-10 w-full rounded-md border border-line bg-panel pr-16 pl-3 text-[14px] focus:border-brand focus:ring-2 focus:ring-brand/15 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute inset-y-0 right-0 px-3 text-[12px] font-medium text-ink-3 hover:text-ink"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            {error && (
              <p role="alert" className="rounded-md border border-crit/25 bg-crit/5 px-3 py-2 text-[12.5px] text-crit">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || !email || !password}
              className="h-10 w-full rounded-md bg-brand text-[14px] font-medium text-white hover:bg-brand/90 disabled:opacity-55"
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p className="text-center text-[11.5px] text-ink-3">
              Accounts are issued by your state or district administrator.
            </p>
          </form>

          <DataNotice className="mt-8 border-t border-line pt-4" />
        </div>
      </main>
    </div>
  );
}

export default function AuthGate() {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    auth
      .me()
      .then(setSession)
      .catch(() => setSession(null))
      .finally(() => setChecking(false));
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
    return (
      <>
        {expired && (
          <div role="status" className="fixed inset-x-0 top-0 z-50 bg-risk px-4 py-2 text-center text-[13px] font-medium text-white">
            Your session ended. Please sign in again.
          </div>
        )}
        <SignIn
          onSignedIn={(s) => {
            setExpired(false);
            setSession(s);
          }}
        />
      </>
    );
  }

  return <App session={session} onSignOut={signOut} />;
}
