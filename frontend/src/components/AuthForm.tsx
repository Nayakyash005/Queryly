import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthLayout } from "./AuthLayout";
import { getSupabase } from "../lib/supabase";

type Mode = "login" | "signup" | "forgot" | "reset";

const COPY: Record<Mode, { title: string; subtitle: string }> = {
  login: { title: "Welcome back", subtitle: "Sign in to pick up where your data takes you." },
  signup: { title: "Create your account", subtitle: "A clearer way to ask questions of your data starts here." },
  forgot: { title: "Reset your password", subtitle: "Enter your email and we’ll send you a secure reset link." },
  reset: { title: "Choose a new password", subtitle: "Use at least 8 characters for your new password." },
};

function readableError(error: unknown) {
  if (error instanceof Error) return error.message;
  return "Something went wrong. Please try again.";
}

export function AuthForm({ mode }: { mode: Mode }) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const validate = () => {
    if (mode !== "reset" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return "Enter a valid email address.";
    if (["login", "signup", "reset"].includes(mode) && password.length < 8) return "Password must be at least 8 characters.";
    if ((mode === "signup" || mode === "reset") && password !== confirmPassword) return "Passwords do not match.";
    return "";
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setNotice("");
    const validationError = validate();
    if (validationError) { setError(validationError); return; }
    setLoading(true);
    try {
      const auth = getSupabase().auth;
      if (mode === "login") {
        const { error: authError } = await auth.signInWithPassword({ email: email.trim(), password });
        if (authError) throw authError;
        navigate("/app", { replace: true });
      } else if (mode === "signup") {
        const { data, error: authError } = await auth.signUp({
          email: email.trim(), password,
          options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
        });
        if (authError) throw authError;
        if (!data.session) setNotice("Check your inbox for a confirmation link to finish creating your account.");
        else navigate("/app", { replace: true });
      } else if (mode === "forgot") {
        const { error: authError } = await auth.resetPasswordForEmail(email.trim(), {
          redirectTo: `${window.location.origin}/reset-password`,
        });
        if (authError) throw authError;
        setNotice("If an account exists for that email, a password reset link is on its way.");
      } else {
        const { error: authError } = await auth.updateUser({ password });
        if (authError) throw authError;
        setNotice("Your password has been updated. You can continue to your workspace.");
      }
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setLoading(false);
    }
  };

  const onGoogle = async () => {
    setError(""); setLoading(true);
    try {
      const { error: authError } = await getSupabase().auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: `${window.location.origin}/auth/callback` },
      });
      if (authError) throw authError;
    } catch (caught) { setError(readableError(caught)); setLoading(false); }
  };

  return (
    <AuthLayout title={COPY[mode].title} subtitle={COPY[mode].subtitle}>
      {mode === "login" && <button className="google-button" type="button" onClick={onGoogle} disabled={loading}>
        <GoogleMark /> Continue with Google
      </button>}
      {mode === "login" && <div className="form-divider"><span>or continue with email</span></div>}
      <form className="auth-form" onSubmit={onSubmit} noValidate>
        {mode !== "reset" && <label className="field"><span>Email address</span><input type="email" autoComplete="email" placeholder="you@company.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>}
        {!["forgot"].includes(mode) && <label className="field"><span>Password</span><input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="At least 8 characters" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={8} /></label>}
        {mode === "login" && <div className="form-meta"><span>Keep your data close.</span><Link to="/forgot-password">Forgot password?</Link></div>}
        {(mode === "signup" || mode === "reset") && <label className="field"><span>Confirm password</span><input type="password" autoComplete="new-password" placeholder="Enter your password again" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required minLength={8} /></label>}
        {error && <p className="form-message error-message" role="alert">{error}</p>}
        {notice && <p className="form-message success-message" role="status">{notice}</p>}
        <button className="primary-button full-button" type="submit" disabled={loading}>{loading ? <><span className="spinner" /> Please wait…</> : mode === "login" ? "Sign in" : mode === "signup" ? "Create account" : mode === "forgot" ? "Send reset link" : "Update password"}</button>
      </form>
      <div className="auth-switch">
        {mode === "login" && <>New to Queryly? <Link to="/signup">Create an account</Link></>}
        {mode === "signup" && <>Already have an account? <Link to="/login">Sign in</Link></>}
        {mode === "forgot" && <>Remembered your password? <Link to="/login">Back to sign in</Link></>}
        {mode === "reset" && <Link to="/login">Return to sign in</Link>}
      </div>
    </AuthLayout>
  );
}

function GoogleMark() {
  return <svg className="google-mark" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M21.35 12.2c0-.68-.06-1.36-.18-2H12v3.8h5.24a4.48 4.48 0 0 1-1.95 2.94v2.47h3.16c1.85-1.7 2.9-4.2 2.9-7.21Z"/><path fill="#34A853" d="M12 21.75c2.65 0 4.88-.88 6.5-2.34l-3.16-2.47c-.88.6-2 .96-3.34.96-2.56 0-4.73-1.73-5.51-4.06H3.22v2.55A9.75 9.75 0 0 0 12 21.75Z"/><path fill="#FBBC05" d="M6.49 13.84a5.86 5.86 0 0 1 0-3.68V7.61H3.22a9.75 9.75 0 0 0 0 8.78l3.27-2.55Z"/><path fill="#EA4335" d="M12 6.1c1.44 0 2.73.5 3.75 1.5l2.81-2.81C16.87 3.2 14.64 2.25 12 2.25a9.75 9.75 0 0 0-8.78 5.36l3.27 2.55C7.27 7.83 9.44 6.1 12 6.1Z"/></svg>;
}
