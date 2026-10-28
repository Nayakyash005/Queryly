import type { ReactNode } from "react";
import { Brand } from "./Brand";

export function AuthLayout({ children, title, subtitle }: { children: ReactNode; title: string; subtitle: string }) {
  return (
    <main className="auth-page">
      <div className="auth-top"><Brand /><a className="back-link" href="/">Back to home <span aria-hidden="true">↗</span></a></div>
      <section className="auth-card">
        <div className="auth-heading"><p className="eyebrow">Your workspace, in plain language</p><h1>{title}</h1><p>{subtitle}</p></div>
        {children}
      </section>
      <p className="auth-footnote">Secure access powered by Supabase Auth</p>
    </main>
  );
}
