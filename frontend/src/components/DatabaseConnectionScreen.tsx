import { useState, type FormEvent } from "react";
import { testDatabaseConnection } from "../lib/api";

type DatabaseKind = "postgresql" | "mysql" | "sqlite";
type SQLiteMode = "memory" | "file";
export type ConnectedDatabase = { connectionUrl: string; dialect: "sqlite" | "postgres" | "mysql"; label: string };
type Props = { onConnected?: (connection: ConnectedDatabase) => Promise<void>; onProgress?: (message: string) => void };

function encodeComponent(value: string) {
  return encodeURIComponent(value).replace(/[!'()*]/g, (character) =>
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

function makeConnectionUrl(kind: DatabaseKind, values: Record<string, string>, sqliteMode: SQLiteMode) {
  if (kind === "sqlite") {
    if (sqliteMode === "memory") return "sqlite+pysqlite:///:memory:";
    const path = values.filePath.trim().replace(/\\/g, "/");
    const segments = path.split("/");
    if (!path || path.startsWith("/") || /^[a-zA-Z]:/.test(path) || segments.some((segment) => !segment || segment === "." || segment === "..")) {
      throw new Error("Enter a relative path inside the backend SQLite data directory (for example, sales.sqlite).");
    }
    return `sqlite+pysqlite:///data/${segments.map(encodeComponent).join("/")}`;
  }

  const host = values.host.trim();
  const port = values.port.trim();
  const database = values.database.trim();
  const username = values.username.trim();
  if (!host || !port || !database || !username || !values.password) {
    throw new Error(`Complete all ${kind === "mysql" ? "MySQL" : "PostgreSQL"} fields before testing the connection.`);
  }
  if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
    throw new Error("Port must be a number between 1 and 65535.");
  }
  // Host is deliberately not treated as URL content. Credentials and database
  // names are percent encoded so reserved characters cannot change URL parsing.
  if (!/^(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\])$/.test(host)) {
    throw new Error("Enter a hostname or IP address without a protocol or port.");
  }
  if (kind === "mysql") {
    return `mysql+pymysql://${encodeComponent(username)}:${encodeComponent(values.password)}@${host}:${port}/${encodeComponent(database)}`;
  }
  return `postgresql+psycopg://${encodeComponent(username)}:${encodeComponent(values.password)}@${host}:${port}/${encodeComponent(database)}?sslmode=require`;
}

export function DatabaseConnectionScreen({ onConnected, onProgress }: Props = {}) {
  const [kind, setKind] = useState<DatabaseKind>("postgresql");
  const [sqliteMode, setSQLiteMode] = useState<SQLiteMode>("memory");
  const [values, setValues] = useState({ host: "", port: "5432", database: "", username: "", password: "", filePath: "" });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState("");

  const update = (field: keyof typeof values) => (event: React.ChangeEvent<HTMLInputElement>) => {
    setValues((current) => ({ ...current, [field]: event.target.value }));
    setResult(null);
    setError("");
  };

  const chooseKind = (next: DatabaseKind) => {
    setKind(next);
    if (next !== "sqlite") setValues((current) => ({ ...current, port: next === "mysql" ? "3306" : "5432" }));
    setResult(null);
    setError("");
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setResult(null);
    setError("");
    let connectionUrl: string;
    try {
      connectionUrl = makeConnectionUrl(kind, values, sqliteMode);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Check the connection details and try again.");
      return;
    }
    setBusy(true);
    try {
      onProgress?.("Connecting");
      const response = await testDatabaseConnection(connectionUrl);
      setResult(response);
      if (response.success && onConnected) {
        const label = kind === "sqlite"
          ? (sqliteMode === "memory" ? "SQLite · In-memory" : `SQLite · ${values.filePath.trim()}`)
          : `${values.database.trim()} · ${values.host.trim()}`;
        const dialect = kind === "sqlite" ? "sqlite" : kind === "mysql" ? "mysql" : "postgres";
        await onConnected({ connectionUrl, dialect, label });
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The connection check could not be completed.");
    } finally {
      setBusy(false);
      onProgress?.("");
    }
  };

  return (
    <main className="connection-page">
      <section className="connection-heading">
        <p className="eyebrow">YOUR PRIVATE WORKSPACE</p>
        <h1>Connect a database</h1>
        <p>Start with a secure connection check. Your credentials are used for this test only and are never saved.</p>
      </section>

      <section className="connection-card" aria-labelledby="connection-form-title">
        <div className="connection-card-heading">
          <div><span className="connection-step">01</span><h2 id="connection-form-title">Choose your database</h2></div>
          <span className="secure-label"><span aria-hidden="true">●</span> Secure check</span>
        </div>
        <div className="database-options" role="group" aria-label="Database type">
          <button type="button" className={`database-option${kind === "postgresql" ? " selected" : ""}`} onClick={() => chooseKind("postgresql")} aria-pressed={kind === "postgresql"}>
            <span className="database-option-icon">P</span><span><strong>PostgreSQL</strong><small>Hosted or managed database</small></span><span className="option-check" aria-hidden="true">✓</span>
          </button>
          <button type="button" className={`database-option${kind === "mysql" ? " selected" : ""}`} onClick={() => chooseKind("mysql")} aria-pressed={kind === "mysql"}>
            <span className="database-option-icon mysql-icon">M</span><span><strong>MySQL</strong><small>Hosted or managed database</small></span><span className="option-check" aria-hidden="true">✓</span>
          </button>
          <button type="button" className={`database-option${kind === "sqlite" ? " selected" : ""}`} onClick={() => chooseKind("sqlite")} aria-pressed={kind === "sqlite"}>
            <span className="database-option-icon sqlite-icon">▦</span><span><strong>SQLite</strong><small>Local file or quick test</small></span><span className="option-check" aria-hidden="true">✓</span>
          </button>
        </div>

        <form className="connection-form" onSubmit={submit}>
          {kind !== "sqlite" ? <>
            <div className="connection-fields">
              <label className="connection-field field-wide">Host<input autoComplete="url" value={values.host} onChange={update("host")} placeholder="db.example.com" required /></label>
              <label className="connection-field field-port">Port<input inputMode="numeric" value={values.port} onChange={update("port")} placeholder={kind === "mysql" ? "3306" : "5432"} required /></label>
              <label className="connection-field">Database name<input value={values.database} onChange={update("database")} placeholder="your_database" required /></label>
              <label className="connection-field">Username<input autoComplete="username" value={values.username} onChange={update("username")} placeholder="database user" required /></label>
              <label className="connection-field field-wide">Password<input type="password" autoComplete="current-password" value={values.password} onChange={update("password")} placeholder="Database password" required /></label>
            </div>
            <p className="connection-hint">Connections use encrypted transport. The API blocks local and private network hosts.</p>
          </> : <>
            <div className="sqlite-modes" role="group" aria-label="SQLite connection type">
              <button type="button" className={sqliteMode === "memory" ? "active" : ""} onClick={() => { setSQLiteMode("memory"); setResult(null); setError(""); }}>In-memory test</button>
              <button type="button" className={sqliteMode === "file" ? "active" : ""} onClick={() => { setSQLiteMode("file"); setResult(null); setError(""); }}>Existing file</button>
            </div>
            {sqliteMode === "memory" ? <div className="sqlite-info"><span className="database-option-icon sqlite-icon">▦</span><div><strong>Temporary SQLite database</strong><p>Creates an in-memory database for this check. It disappears when the test finishes.</p></div></div> : <>
              <label className="connection-field">File path relative to backend/data<input value={values.filePath} onChange={update("filePath")} placeholder="sales.sqlite" required /><small>Use an existing file. The backend opens it read-only and rejects paths outside its allowed data directory.</small></label>
            </>}
          </>}

          {error && <p className="form-message error-message connection-message" role="alert">{error}</p>}
          {result && <p className={`form-message connection-message ${result.success ? "success-message" : "error-message"}`} role="status">{result.message}</p>}
          <div className="connection-submit-row"><p>Connection details are not stored.</p><button className="primary-button connection-submit" type="submit" disabled={busy}>{busy ? <><span className="spinner" /> {onConnected ? "Connecting…" : "Testing…"}</> : onConnected ? "Connect and inspect" : "Test connection"}</button></div>
        </form>
      </section>
      <p className="connection-footnote"><span aria-hidden="true">⌑</span> Your database credentials stay in this session and are sent only to the Queryly API for verification.</p>
    </main>
  );
}
