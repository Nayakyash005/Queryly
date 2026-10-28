import { Link } from "react-router-dom";

export function Brand({ light = false }: { light?: boolean }) {
  return (
    <Link className={`brand${light ? " brand-light" : ""}`} to="/" aria-label="Queryly home">
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" fill="none">
          <path d="M7 8.5h18M7 16h12M7 23.5h8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
          <circle cx="24" cy="22" r="4" stroke="currentColor" strokeWidth="2.2" />
          <path d="m27 25 2 2" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
      </span>
      <span>queryly</span>
    </Link>
  );
}
