import { useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import type { Session } from "@supabase/supabase-js";
import { AuthForm } from "./components/AuthForm";
import { Brand } from "./components/Brand";
import { QueryWorkspace } from "./components/QueryWorkspace";
import { getSupabase, isSupabaseConfigured, supabase } from "./lib/supabase";

function LandingPage() {
  return <div className="landing-page">
    <header className="site-header"><div className="nav-wrap"><Brand /><nav className="main-nav" aria-label="Main navigation"><a href="#features">Features</a><a href="#how-it-works">How it works</a></nav><div className="nav-actions"><Link className="nav-login" to="/login">Log in</Link><Link className="button button-small" to="/signup">Get started <span aria-hidden="true">↗</span></Link></div></div></header>
    <main>
      <section className="hero-section">
        <div className="hero-copy"><p className="hero-kicker"><span className="live-dot" /> YOUR DATA, IN YOUR WORDS</p><h1>Meet your new<br /><span>favorite analyst.</span></h1><p className="hero-description">Ask questions in plain English. Get clear answers from your database in seconds, without writing a single query.</p><div className="hero-ctas"><Link className="button" to="/signup">Get started free <span aria-hidden="true">↗</span></Link><a className="text-cta" href="#how-it-works">See how it works <span aria-hidden="true">↓</span></a></div><div className="hero-proof"><div className="proof-avatars" aria-hidden="true"><span>J</span><span>M</span><span>A</span><span>+</span></div><p><strong>Built for curious teams</strong><br />From first question to useful insight</p></div></div>
        <div className="hero-visual" aria-label="Example of an AI data assistant answering a question"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="glow-orb"><div className="orb-core"><span className="orb-spark">✳</span></div></div><div className="query-card"><div className="query-head"><span className="query-avatar">q.</span><span>Ask your data</span><span className="query-dots">•••</span></div><p className="query-question">Which products grew the most this quarter?</p><div className="query-answer"><span className="answer-icon">↗</span><div><small>REVENUE INSIGHT</small><strong>Cloud Sync <em>+38.4%</em></strong><span>Top growth across 12 products</span></div></div><div className="mini-chart"><div className="chart-label"><span>Revenue by product</span><span>Q3 · 2025</span></div><div className="chart-bars"><i style={{height:"34%"}} /><i style={{height:"48%"}} /><i style={{height:"42%"}} /><i style={{height:"64%"}} /><i style={{height:"56%"}} /><i style={{height:"78%"}} /><i style={{height:"100%"}} /></div><div className="chart-x"><span>JUL</span><span>AUG</span><span>SEP</span></div></div><div className="query-footer"><span className="check-icon">✓</span> Answered from your live data <span>·</span> 0.8s</div></div><div className="float-chip chip-secure"><span>⌑</span> Private by design</div><div className="float-chip chip-spark"><span>✦</span> Insight found</div></div>
      </section>
      <section className="trust-strip"><p>BUILT FOR TEAMS THAT RUN ON DATA</p><div className="trust-items"><span>✳ &nbsp;Northstar</span><span>◈ &nbsp;layered</span><span>▱ &nbsp;common room</span><span>✣ &nbsp;brightline</span><span>◉ &nbsp;daybreak</span></div></section>
      <section className="feature-section" id="features"><div className="section-intro"><p className="eyebrow">LESS QUERYING. MORE KNOWING.</p><h2>Everything you need<br />to get to the <em>why.</em></h2><p>Move from raw data to confident decisions, with an assistant that speaks your language.</p></div><div className="feature-grid"><article className="feature-card feature-card-large"><div className="feature-icon icon-violet">✳</div><h3>Ask naturally</h3><p>Skip the syntax. Ask real questions the way you’d ask a teammate, and get answers grounded in your data.</p><div className="feature-quote"><span>“What changed in signups last month?”</span><b>↗</b></div></article><article className="feature-card"><div className="feature-icon icon-mint">⌁</div><h3>Stay in the loop</h3><p>See the SQL behind each answer and understand exactly where your insight came from.</p><div className="code-lines"><i /><i /><i /></div></article><article className="feature-card"><div className="feature-icon icon-peach">⛨</div><h3>Your data stays yours</h3><p>Connect with confidence. Your access is secure and only the data you’re authorized to see is in reach.</p><div className="privacy-lock">⌑ &nbsp;Encrypted connection <span>✓</span></div></article></div></section>
      <section className="how-section" id="how-it-works"><div><p className="eyebrow">THREE STEPS TO CLARITY</p><h2>From question<br />to <em>“aha.”</em></h2></div><div className="steps"><article><span>01</span><div><h3>Connect your database</h3><p>Bring your data source into a workspace built for secure exploration.</p></div></article><article><span>02</span><div><h3>Ask what you want to know</h3><p>Write a question in your own words. No SQL knowledge required.</p></div></article><article><span>03</span><div><h3>Get a clear, explainable answer</h3><p>Explore results and see the query behind the insight.</p></div></article></div></section>
      <section className="closing-cta"><div className="closing-stars" aria-hidden="true">✳ &nbsp; · &nbsp; ✦</div><p className="eyebrow">YOUR NEXT GOOD QUESTION STARTS HERE</p><h2>Make data feel<br /><em>like a conversation.</em></h2><Link className="button button-light" to="/signup">Get started free <span aria-hidden="true">↗</span></Link><p>No credit card required</p></section>
    </main><footer className="site-footer"><Brand /><span>© 2025 Queryly. Built for better questions.</span><div><Link to="/login">Log in</Link><Link to="/signup">Get started</Link></div></footer>
  </div>;
}

function AuthCallback() {
  const navigate = useNavigate();
  const [message, setMessage] = useState("Confirming your sign-in…");
  useEffect(() => {
    let active = true;
    if (!supabase) { setMessage("Authentication is not configured."); return; }
    supabase.auth.getSession().then(({ data, error }) => {
      if (!active) return;
      if (error || !data.session) setMessage(error?.message || "This sign-in link is invalid or has expired.");
      else navigate("/app", { replace: true });
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (session) navigate("/app", { replace: true });
    });
    return () => { active = false; listener.subscription.unsubscribe(); };
  }, [navigate]);
  return <main className="callback-page"><div className="callback-card"><Brand /><span className="spinner callback-spinner" /><h1>{message}</h1><p>If this takes too long, return to sign in and try again.</p><Link to="/login">Go to sign in</Link></div></main>;
}

function Workspace() {
  const navigate = useNavigate();
  const signOut = async () => { await getSupabase().auth.signOut(); navigate("/", { replace: true }); };
  return <div className="workspace-page"><header className="workspace-nav"><Brand /><button className="text-button" onClick={signOut}>Sign out</button></header><QueryWorkspace /></div>;
}

function ProtectedWorkspace() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!supabase) { setReady(true); return; }
    getSupabase().auth.getSession().then(({ data }) => { setSession(data.session); setReady(true); });
    const { data: listener } = getSupabase().auth.onAuthStateChange((_event, current) => setSession(current));
    return () => listener.subscription.unsubscribe();
  }, []);
  if (!ready) return <main className="loading-page"><span className="spinner" /> Loading your workspace…</main>;
  return session ? <Workspace /> : <Navigate to="/login" replace />;
}

function MissingConfig() {
  if (isSupabaseConfigured) return null;
  return <div className="config-banner" role="alert">Add VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY to frontend/.env.local, then restart Vite.</div>;
}

function AppRoutes() {
  return <><MissingConfig /><Routes>
    <Route path="/" element={<LandingPage />} />
    <Route path="/login" element={<AuthForm mode="login" />} />
    <Route path="/signup" element={<AuthForm mode="signup" />} />
    <Route path="/forgot-password" element={<AuthForm mode="forgot" />} />
    <Route path="/reset-password" element={<AuthForm mode="reset" />} />
    <Route path="/auth/callback" element={<AuthCallback />} />
    <Route path="/app" element={<ProtectedWorkspace />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></>;
}

function App() { return <BrowserRouter><AppRoutes /></BrowserRouter>; }

export default App;
