import { useState } from "react";
import {
  DatabaseConnectionScreen,
  type ConnectedDatabase,
} from "./DatabaseConnectionScreen";
import {
  executeSQLWithProgress,
  generateSQL,
  inspectDatabaseSchema,
  runAgent,
  type DatabaseSchema,
  type QueryResults,
} from "../lib/api";

type Progress =
  | "Connecting"
  | "Inspecting schema"
  | "Generating SQL"
  | "Validating query"
  | "Executing query"
  | "Analyzing schema"
  | "Drafting SQL"
  | "Validating SQL"
  | "Repairing SQL"
  | "SQL issue detected"
  | "Query complete"
  | "Query failed"
  | "";
type StageStatus = "active" | "passed" | "error" | "recovered";
type VisibleStage = { stage: string; label: string; status: StageStatus };

export function QueryWorkspace() {
  const [connection, setConnection] = useState<ConnectedDatabase | null>(null);
  const [schema, setSchema] = useState<DatabaseSchema | null>(null);
  const [selectedTable, setSelectedTable] = useState("");
  const [question, setQuestion] = useState("");
  const [sql, setSql] = useState("");
  const [explanation, setExplanation] = useState("");
  const [results, setResults] = useState<QueryResults | null>(null);
  const [progress, setProgress] = useState<Progress>("");
  const [error, setError] = useState("");
  const [agentNote, setAgentNote] = useState("");
  const [agentStages, setAgentStages] = useState<VisibleStage[]>([]);
  const [exportingResults, setExportingResults] = useState(false);

  const recordStage = (stage: string, label: Progress) => {
    setAgentStages((current) => {
      const next = current.map((item) => ({ ...item }));
      const last = next[next.length - 1];
      if (stage === "sql_error") {
        if (last?.status === "active") last.status = "error";
      } else if (stage === "failed") {
        if (last?.status === "active") last.status = "error";
      } else if (stage === "executing_query" || stage === "completed") {
        if (last?.status === "active") last.status = "passed";
        for (let index = next.length - 1; index >= 0; index -= 1) {
          if (
            next[index].stage === "sql_error" &&
            next[index].status === "error"
          ) {
            next[index].status = "recovered";
            if (index > 0 && next[index - 1].status === "error")
              next[index - 1].status = "recovered";
            break;
          }
        }
      } else if (last?.status === "active") {
        last.status = "passed";
      }
      next.push({
        stage,
        label,
        status:
          stage === "completed"
            ? "passed"
            : stage === "sql_error" || stage === "failed"
              ? "error"
              : "active",
      });
      return next;
    });
  };

  const connect = async (next: ConnectedDatabase) => {
    setProgress("Inspecting schema");
    setError("");
    const inspected = await inspectDatabaseSchema(next.connectionUrl);
    setConnection(next);
    setSchema(inspected);
    setSelectedTable(inspected.tables[0]?.name || "");
    setQuestion("");
    setSql("");
    setExplanation("");
    setResults(null);
  };

  const generate = async () => {
    if (!schema || !question.trim()) return;
    setError("");
    setAgentNote("");
    setResults(null);
    setProgress("Generating SQL");
    try {
      const generated = await generateSQL(
        question.trim(),
        schema,
        connection!.dialect,
      );
      setSql(generated.query);
      setExplanation(generated.explanation);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "SQL generation could not be completed.",
      );
    } finally {
      setProgress("");
    }
  };

  const run = async () => {
    if (!connection || !schema || !sql.trim()) return;
    setError("");
    setAgentNote("");
    setResults(null);
    setAgentStages([]);
    const labels: Record<string, Progress> = {
      analyzing_schema: "Analyzing schema",
      validating_sql: "Validating SQL",
      executing_query: "Executing query",
      repairing_sql: "Repairing SQL",
      sql_error: "SQL issue detected",
      completed: "Query complete",
      failed: "Query failed",
    };
    setProgress("Validating SQL");
    try {
      const response = await executeSQLWithProgress(
        connection.connectionUrl,
        sql,
        schema,
        connection.dialect,
        question.trim(),
        (stage) => {
          const label = labels[stage] ?? stage;
          setProgress(label);
          recordStage(stage, label);
        },
      );
      if (!response.success)
        setError(response.error || "The query could not be completed.");
      else {
        if (response.repaired && response.query) {
          setSql(response.query);
          if (response.explanation) setExplanation(response.explanation);
          setAgentNote(
            `Queryly corrected the SQL after ${response.attempt_count ?? 1} repair ${(response.attempt_count ?? 1) === 1 ? "attempt" : "attempts"}.`,
          );
        }
        setResults(response);
      }
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Query execution could not be completed.",
      );
    } finally {
      setProgress("");
    }
  };

  const downloadResultsAsExcel = async () => {
    if (!results?.success || results.rows.length === 0) return;
    setError("");
    setExportingResults(true);
    try {
      const XLSX = await import("xlsx");
      const worksheetData = [
        results.columns,
        ...results.rows.map((row) =>
          results.columns.map((_, columnIndex) => {
            const value = row[columnIndex];
            if (value === null || value === undefined) return null;
            if (typeof value === "object")
              return JSON.stringify(value) ?? String(value);
            if (typeof value === "number" && !Number.isFinite(value))
              return String(value);
            return value;
          }),
        ),
      ];
      const worksheet = XLSX.utils.aoa_to_sheet(worksheetData);
      worksheet["!cols"] = results.columns.map((column, columnIndex) => {
        const longestValue = results.rows.reduce((longest, row) => {
          const value = row[columnIndex];
          const text =
            value === null || value === undefined
              ? ""
              : typeof value === "object"
                ? (JSON.stringify(value) ?? String(value))
                : String(value);
          return Math.max(longest, text.length);
        }, column.length);
        return { wch: Math.min(48, Math.max(12, longestValue + 2)) };
      });

      const workbook = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(workbook, worksheet, "Query Results");
      XLSX.writeFileXLSX(
        workbook,
        `queryly-results-${new Date().toISOString().slice(0, 10)}.xlsx`,
        {
          compression: true,
        },
      );
    } catch {
      setError("Could not create the Excel file. Please try again.");
    } finally {
      setExportingResults(false);
    }
  };

  const askAgent = async () => {
    if (!connection || !question.trim()) return;
    setError("");
    setResults(null);
    setAgentNote("");
    setAgentStages([]);
    const labels: Record<string, Progress> = {
      analyzing_schema: "Analyzing schema",
      drafting_sql: "Drafting SQL",
      validating_sql: "Validating SQL",
      executing_query: "Executing query",
      repairing_sql: "Repairing SQL",
      sql_error: "SQL issue detected",
      completed: "Query complete",
      failed: "Query failed",
    };
    try {
      const response = await runAgent(
        connection.connectionUrl,
        question.trim(),
        connection.dialect,
        (stage) => {
          const label = labels[stage] ?? stage;
          setProgress(label);
          recordStage(stage, label);
        },
        (failure) => {
          if (failure.query) setSql(failure.query);
          if (failure.explanation) setExplanation(failure.explanation);
        },
      );
      setSql(response.query);
      setExplanation(response.explanation);
      setResults({
        success: true,
        columns: response.columns,
        rows: response.rows,
        row_count: response.row_count,
        truncated: response.truncated,
        error: null,
      });
      setAgentNote(
        response.attempt_count
          ? `Completed after ${response.attempt_count} SQL repair ${response.attempt_count === 1 ? "attempt" : "attempts"}.`
          : "Completed without SQL repairs.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The agent could not complete this query.",
      );
    } finally {
      setProgress("");
    }
  };

  if (!connection || !schema) {
    return (
      <>
        {progress && (
          <p className="workspace-progress">
            <span className="spinner" /> {progress}
          </p>
        )}
        <DatabaseConnectionScreen
          onConnected={connect}
          onProgress={(message) => setProgress(message as Progress)}
        />
      </>
    );
  }

  const activeTable = schema.tables.find(
    (table) => table.name === selectedTable,
  );
  return (
    <main className="query-workspace">
      <aside className="query-sidebar">
        <div className="sidebar-section-title">
          <span>DATA SOURCE</span>
          <button
            className="sidebar-action"
            onClick={() => {
              setConnection(null);
              setSchema(null);
              setResults(null);
              setSql("");
              setError("");
            }}
          >
            Change
          </button>
        </div>
        <div className="connected-database">
          <span className="connected-icon">
            {connection.dialect === "sqlite"
              ? "▦"
              : connection.dialect === "mysql"
                ? "M"
                : "P"}
          </span>
          <div>
            <strong>
              {connection.dialect === "sqlite"
                ? "SQLite"
                : connection.dialect === "mysql"
                  ? "MySQL"
                  : "PostgreSQL"}
            </strong>
            <small>{connection.label.replace(/^[^·]+·\s*/, "")}</small>
          </div>
          <span className="connected-dot" title="Connected" />
        </div>
        <div className="sidebar-section-title tables-heading">
          <span>TABLES · {schema.tables.length}</span>
        </div>
        {schema.tables.length ? (
          <div className="table-list">
            {schema.tables.map((table) => (
              <button
                key={`${table.schema || ""}.${table.name}`}
                className={`table-item${selectedTable === table.name ? " active" : ""}`}
                onClick={() => setSelectedTable(table.name)}
              >
                <span aria-hidden="true">▤</span>
                <span>{table.name}</span>
                <small>{table.columns.length}</small>
              </button>
            ))}
          </div>
        ) : (
          <p className="empty-schema">No tables found in this database.</p>
        )}
        {activeTable && (
          <section className="table-details">
            <h3>{activeTable.name}</h3>
            {activeTable.columns.map((column) => (
              <div className="column-item" key={column.name}>
                <span>
                  {column.name}
                  {column.primary_key && <b title="Primary key"> PK</b>}
                </span>
                <small>{column.data_type}</small>
              </div>
            ))}
            {activeTable.relationships.map((relation) => (
              <p
                className="relationship"
                key={`${relation.to_table}.${relation.from_columns.join(",")}`}
              >
                ↳ {relation.from_columns.join(", ")} → {relation.to_table}.
                {relation.to_columns.join(", ")}
              </p>
            ))}
          </section>
        )}
        <p className="sidebar-privacy">
          Credentials are held in memory for this session only.
        </p>
      </aside>

      <section className="query-main">
        <div className="query-title-row">
          <div>
            <p className="eyebrow">QUERY WORKSPACE</p>
            <h1>Ask your data</h1>
            <p>
              Describe what you want to know. Queryly will draft SQL from the
              inspected schema.
            </p>
          </div>
          <span className="schema-count">
            {schema.tables.length}{" "}
            {schema.tables.length === 1 ? "table" : "tables"} inspected
          </span>
        </div>
        <section className="workspace-card question-card">
          <label htmlFor="data-question">YOUR QUESTION</label>
          <textarea
            id="data-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="e.g. Which customers placed the most orders?"
            maxLength={4000}
          />
          <div className="question-footer">
            <span>Ask in plain language · {question.length}/4000</span>
            <div className="question-actions">
              <button
                className="secondary-workspace-button"
                onClick={generate}
                disabled={!question.trim() || Boolean(progress)}
              >
                {progress === "Generating SQL" ? (
                  <>
                    <span className="spinner" /> Generating SQL
                  </>
                ) : (
                  "Generate SQL"
                )}
              </button>
              <button
                className="primary-button workspace-button agent-button"
                onClick={askAgent}
                disabled={!question.trim() || Boolean(progress)}
              >
                {progress &&
                ![
                  "Generating SQL",
                  "Validating query",
                  "Executing query",
                ].includes(progress) ? (
                  <>
                    <span className="spinner" /> {progress}
                  </>
                ) : (
                  "Ask Queryly  ✦"
                )}
              </button>
            </div>
          </div>
        </section>

        {progress && (
          <div className="workspace-progress inline-progress" role="status">
            <span className="spinner" /> {progress}
          </div>
        )}
        {agentStages.length > 0 && (
          <ol
            className="agent-stage-history"
            aria-label="Query progress history"
            aria-live="polite"
          >
            {agentStages.map((item, index) => (
              <li
                className={`agent-stage stage-${item.stage} status-${item.status}`}
                key={`${item.stage}-${index}`}
              >
                <span className="stage-marker">
                  {item.status === "passed" || item.status === "recovered"
                    ? "✓"
                    : item.status === "error"
                      ? "!"
                      : index + 1}
                </span>
                <span>{item.label}</span>
              </li>
            ))}
          </ol>
        )}
        {error && (
          <p
            className="form-message error-message workspace-error"
            role="alert"
          >
            {error}
          </p>
        )}

        <section className="workspace-card sql-card">
          <div className="panel-heading">
            <div>
              <span className="panel-index">01</span>
              <div>
                <h2>SQL preview</h2>
                <p>
                  {explanation ||
                    "Generated SQL appears here. Review or edit it before execution."}
                </p>
              </div>
            </div>
            <span className="dialect-badge">
              {connection.dialect === "sqlite"
                ? "SQLite"
                : connection.dialect === "mysql"
                  ? "MySQL"
                  : "PostgreSQL"}
            </span>
          </div>
          <textarea
            className="sql-editor"
            aria-label="SQL query"
            spellCheck={false}
            value={sql}
            onChange={(event) => {
              setSql(event.target.value);
              setResults(null);
            }}
            placeholder="Your generated SELECT query will appear here…"
          />
          <div className="sql-footer">
            <span>Queries are validated on the server before execution.</span>
            <button
              className="primary-button workspace-button run-button"
              onClick={run}
              disabled={!sql.trim() || Boolean(progress)}
            >
              {["Validating SQL", "Executing query", "Repairing SQL"].includes(
                progress,
              ) ? (
                <>
                  <span className="spinner" /> {progress}
                </>
              ) : (
                "Run query  ▶"
              )}
            </button>
          </div>
        </section>

        <section className="workspace-card results-card">
          <div className="panel-heading results-heading">
            <div>
              <span className="panel-index">02</span>
              <div>
                <h2>Results</h2>
                <p>
                  {results
                    ? `${results.row_count} rows returned${results.truncated ? " | result limit reached" : ""}`
                    : "Your query results will appear here."}
                  {agentNote && (
                    <span className="agent-note"> | {agentNote}</span>
                  )}
                </p>
              </div>
            </div>
            {results && (
              <span className="result-count">{results.row_count} rows</span>
            )}
          </div>
          {results ? (
            <>
              <div className="results-table-wrap">
                <table className="results-table">
                  <thead>
                    <tr>
                      {results.columns.map((column, index) => (
                        <th key={`${column}-${index}`}>{column}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {results.rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {row.map((value, colIndex) => (
                          <td key={colIndex}>
                            {value === null ? (
                              <span className="null-value">NULL</span>
                            ) : typeof value === "object" ? (
                              JSON.stringify(value)
                            ) : (
                              String(value)
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {results.truncated && (
                <p className="truncated-note">
                  Showing the first {results.row_count} rows. More matching rows
                  were available.
                </p>
              )}
              {results.success && results.rows.length > 0 && (
                <div className="results-export-footer">
                  <span>
                    {results.truncated
                      ? "Export includes the rows returned here; more matching rows were omitted by the result limit."
                      : `Export all ${results.rows.length} returned rows.`}
                  </span>
                  <button
                    className="secondary-workspace-button"
                    onClick={downloadResultsAsExcel}
                    disabled={exportingResults}
                  >
                    {exportingResults ? (
                      <>
                        <span className="spinner" /> Preparing Excel
                      </>
                    ) : (
                      "Download as Excel"
                    )}
                  </button>
                </div>
              )}
            </>
          ) : (
            <div className="results-empty">
              <span aria-hidden="true">□</span>
              <p>Run a query to explore your data.</p>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
