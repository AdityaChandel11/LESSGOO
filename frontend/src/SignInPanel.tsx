/**
 * The way in — the demo roles, then the department form.
 *
 * Lives on its own because two pages need exactly this and neither should own
 * it. The front door puts it in the white half of the split; the expired-
 * session page puts it in the middle of an otherwise empty screen. A second
 * copy of a sign-in form is a second place for a validation rule or an error
 * message to drift out of step.
 *
 * The demo roles come first, above the form, because most people arriving here
 * have no account and never will — a judge, an officer being shown the thing,
 * a colleague following a link. Making them read past a password box to find
 * that out is the small rudeness that loses the first thirty seconds.
 */

import { type FormEvent, useEffect, useState } from "react";

import DataNotice from "./DataNotice";
import { ApiError, type DemoAccount, ROLE_LABEL, type Session, auth } from "./api";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SignInPanel({
  onSignedIn,
  showNotice = true,
}: {
  onSignedIn: (s: Session) => void;
  /** The front door carries its own notice in the footer; the bare page does not. */
  showNotice?: boolean;
}) {
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
            ? "Email or password is incorrect. To explore the demo, use one of the Continue buttons above — no password needed."
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
    <div className="w-full max-w-[400px]">
      {demoAccounts.length > 0 && (
        <section aria-labelledby="demo-heading" className="mb-8">
          <h2 id="demo-heading" className="text-[22px] font-semibold tracking-tight">
            Explore the demo
          </h2>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
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
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-line bg-panel px-3.5 py-3 text-left transition-colors hover:border-brand/50 hover:bg-brand/[0.03] focus:ring-2 focus:ring-brand/25 focus:outline-none disabled:opacity-60"
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
          <p
            role="alert"
            className="rounded-md border border-crit/25 bg-crit/5 px-3 py-2 text-[12.5px] text-crit"
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy || !email || !password}
          className="h-10 w-full rounded-md bg-brand text-[14px] font-medium text-white hover:bg-brand/90 focus:ring-2 focus:ring-brand/30 focus:ring-offset-2 focus:outline-none disabled:opacity-55"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-center text-[11.5px] text-ink-3">
          Accounts are issued by your state or district administrator.
        </p>
      </form>

      {showNotice && <DataNotice className="mt-8 border-t border-line pt-4" />}
    </div>
  );
}
