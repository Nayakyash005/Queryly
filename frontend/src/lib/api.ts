import { getSupabase } from "./supabase";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export async function fetchCurrentUser() {
  const { data, error } = await getSupabase().auth.getSession();
  if (error) throw error;
  const token = data.session?.access_token;
  if (!token) throw new Error("Your session has expired. Please sign in again.");

  const response = await fetch(`${apiBaseUrl}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "Could not verify your session with the API.");
  }
  return (await response.json()) as { id: string; email: string | null; role: string | null };
}

export type ConnectionTestResult = { success: boolean; message: string };

export type SchemaColumn = { name: string; data_type: string; nullable: boolean; primary_key: boolean };
export type SchemaTable = {
  name: string;
  schema?: string | null;
  columns: SchemaColumn[];
  primary_key: { name?: string | null; columns: string[] };
  foreign_keys: Array<{ name?: string | null; columns: string[]; referred_schema?: string | null; referred_table: string; referred_columns: string[] }>;
  relationships: Array<{ from_columns: string[]; to_schema?: string | null; to_table: string; to_columns: string[] }>;
};
export type DatabaseSchema = { tables: SchemaTable[] };
export type GeneratedSQL = { query: string; explanation: string };
export type QueryResults = { success: boolean; columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean; error: string | null; query?: string | null; explanation?: string | null; attempt_count?: number; repaired?: boolean };
export type AgentResult = { query: string; explanation: string; columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean; attempt_count: number };
export type AgentFailure = { query: string; explanation: string; attempt_count: number };

async function authenticatedPost<T>(path: string, body: unknown): Promise<T> {
  const { data, error } = await getSupabase().auth.getSession();
  if (error) throw new Error(error.message || "Could not read your sign-in session.");
  const token = data.session?.access_token;
  if (!token) throw new Error("Your session has expired. Please sign in again.");
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Could not reach the Queryly API. Check that the backend is running.");
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof payload?.detail === "string"
      ? payload.detail
      : Array.isArray(payload?.detail) && typeof payload.detail[0]?.msg === "string"
        ? payload.detail[0].msg
        : "The request could not be completed.";
    throw new Error(detail);
  }
  return payload as T;
}

export function inspectDatabaseSchema(connectionUrl: string) {
  return authenticatedPost<DatabaseSchema>("/api/connections/schema", { connection_url: connectionUrl });
}

export function generateSQL(question: string, schema: DatabaseSchema, dialect: string) {
  return authenticatedPost<GeneratedSQL>("/api/query/generate", { question, schema, dialect });
}

export function executeSQL(connectionUrl: string, query: string, schema: DatabaseSchema, dialect: string, question: string) {
  return authenticatedPost<QueryResults>("/api/query/execute", { connection_url: connectionUrl, query, schema, dialect, question });
}

export async function executeSQLWithProgress(
  connectionUrl: string,
  query: string,
  schema: DatabaseSchema,
  dialect: string,
  question: string,
  onStage: (stage: string) => void,
): Promise<QueryResults> {
  const { data, error } = await getSupabase().auth.getSession();
  if (error) throw new Error(error.message || "Could not read your sign-in session.");
  const token = data.session?.access_token;
  if (!token) throw new Error("Your session has expired. Please sign in again.");

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/api/query/execute/stream`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ connection_url: connectionUrl, query, schema, dialect, question }),
    });
  } catch {
    throw new Error("Could not reach the Queryly API. Check that the backend is running.");
  }
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    const detail = typeof payload?.detail === "string" ? payload.detail : "The query request could not be completed.";
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: QueryResults | null = null;
  let failure = "";
  const consume = async (block: string) => {
    let eventName = "message";
    let data = "";
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    const payload = JSON.parse(data);
    if (eventName === "stage" && typeof payload.stage === "string") {
      onStage(payload.stage);
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    } else if (eventName === "result") {
      result = payload as QueryResults;
    } else if (eventName === "failed") {
      failure = typeof payload.error === "string" ? payload.error : "The query could not be completed.";
    }
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) await consume(block);
      if (done) break;
    }
    if (buffer.trim()) await consume(buffer);
  } finally {
    reader.releaseLock();
  }
  if (failure) throw new Error(failure);
  if (!result) throw new Error("The query response ended before returning results.");
  return result;
}

export async function runAgent(
  connectionUrl: string,
  question: string,
  dialect: string,
  onStage: (stage: string) => void,
  onFailure?: (failure: AgentFailure) => void,
): Promise<AgentResult> {
  const { data, error } = await getSupabase().auth.getSession();
  if (error) throw new Error(error.message || "Could not read your sign-in session.");
  const token = data.session?.access_token;
  if (!token) throw new Error("Your session has expired. Please sign in again.");

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/api/query/agent`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ connection_url: connectionUrl, question, dialect }),
    });
  } catch {
    throw new Error("Could not reach the Queryly API. Check that the backend is running.");
  }
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "The agent request could not be completed.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AgentResult | null = null;
  let failure = "";
  const consume = async (block: string) => {
    let eventName = "message";
    let data = "";
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    const payload = JSON.parse(data);
    if (eventName === "stage" && typeof payload.stage === "string") {
      onStage(payload.stage);
      // Let React commit and the browser paint every stage before consuming
      // more events that may already be buffered in the same network chunk.
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    }
    else if (eventName === "result") result = payload as AgentResult;
    else if (eventName === "failed") {
      onFailure?.({
        query: typeof payload.query === "string" ? payload.query : "",
        explanation: typeof payload.explanation === "string" ? payload.explanation : "",
        attempt_count: typeof payload.attempt_count === "number" ? payload.attempt_count : 0,
      });
      failure = typeof payload.error === "string" ? payload.error : "The agent could not complete this query.";
    }
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) await consume(block);
      if (done) break;
    }
    if (buffer.trim()) await consume(buffer);
  } finally {
    reader.releaseLock();
  }
  if (failure) throw new Error(failure);
  if (!result) throw new Error("The agent response ended before returning query results.");
  return result;
}

export async function testDatabaseConnection(connectionUrl: string): Promise<ConnectionTestResult> {
  const { data, error } = await getSupabase().auth.getSession();
  if (error) throw new Error(error.message || "Could not read your sign-in session.");
  const token = data.session?.access_token;
  if (!token) throw new Error("Your session has expired. Please sign in again.");

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/api/connections/test`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ connection_url: connectionUrl }),
    });
  } catch {
    throw new Error("Could not reach the Queryly API. Check that the backend is running.");
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof body?.detail === "string"
      ? body.detail
      : Array.isArray(body?.detail) && typeof body.detail[0]?.msg === "string"
        ? body.detail[0].msg
        : "The connection check could not be completed.";
    throw new Error(detail);
  }
  if (typeof body?.success !== "boolean" || typeof body?.message !== "string") {
    throw new Error("The API returned an unexpected response.");
  }
  return body as ConnectionTestResult;
}
