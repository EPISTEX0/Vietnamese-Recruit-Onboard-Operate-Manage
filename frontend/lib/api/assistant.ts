/**
 * API client for the AI Assistant module.
 *
 * Conversation history is held in React state (not persisted, per grill decision).
 * Each request sends the full history; backend processes statelessly.
 */

    import { API_BASE_URL } from "./client";
    import { ApiError } from "./types";

const BASE = `${API_BASE_URL}/api/assistant`;
const TIMEOUT_MS = 60_000; // LLM calls can be slow

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------


export interface AssistantFeedbackRequest {
  /** Client-generated session identifier */
  session_id: string;
  /** Index of the message this feedback is for */
  message_index: number;
  /** Thumbs up or thumbs down */
  feedback_type: "up" | "down";
  /** Optional free-text explanation */
  optional_text?: string;
}
export interface ChatMessage {
  role: "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: Array<{
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }>;
  tool_call_id?: string;
  name?: string;
}

export interface DraftAction {
  action_type: string;
  parameters: Record<string, unknown>;
  preview: string;
  provenance?: Record<string, unknown>;
  confirm_endpoint: string;
  confirm_method: string;
  confirm_body: Record<string, unknown>;
}

export interface ChatResponse {
  messages: ChatMessage[];
  draft_action: DraftAction | null;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export type StreamCallback = (event: SSEEvent) => void;


export interface SessionStartResponse {
  session_id: string;
}

interface ChatRequestMessage {
  role: "user" | "assistant";
  content: string;
}

// ---------------------------------------------------------------------------
// API Functions
// ---------------------------------------------------------------------------

    async function fetchWithTimeout(
      url: string,
      init?: RequestInit,
    ): Promise<Response> {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
      try {
        return await fetch(url, { ...init, signal: controller.signal, credentials: "include" });
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          throw new ApiError(0, "TIMEOUT", "Yêu cầu đã hết thời gian chờ");
        }
        throw new ApiError(0, "NETWORK_ERROR", "Lỗi kết nối mạng");
      } finally {
        clearTimeout(timeout);
      }
    }

/**
 * Send a chat message to the AI Assistant.
 *
 * @param messages - Full conversation history. Last message must be from user.
 * @returns New messages from this turn + optional Draft Action.
 */

/**
 * Send a chat message via SSE streaming.
 *
 * Opens a POST connection to /chat/stream and reads Server-Sent Events.
 * Calls onEvent for each event (text_delta, tool_start, tool_end,
 * draft_action, done, error).
 *
 * Returns an abort function to cancel the stream.
 */
export function sendStreamMessage(
  messages: ChatMessage[],
  onEvent: StreamCallback,
  onError: (error: Error) => void,
  onDone: () => void,
  sessionId?: string,
): () => void {
  const sanitized: ChatRequestMessage[] = messages.flatMap((message) => {
    if (message.role === "tool") return [];
    if (message.content === null) return [];
    const content = message.content.trim();
    if (!content) return [];
    return [{ role: message.role, content }];
  });

  const body: Record<string, unknown> = { messages: sanitized };
  if (sessionId) {
    body.session_id = sessionId;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);

  fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
    credentials: "include",
  })
    .then(async (res) => {
      if (!res.ok) {
        const text = await res.text().catch(() => "Unknown error");
        onError(new Error(`Assistant API ${res.status}: ${text}`));
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        onError(new Error("Stream not supported"));
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      let streamDone = false;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          let eventName = "";
          let eventData = "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              eventName = line.slice(7);
            } else if (line.startsWith("data: ")) {
              eventData = line.slice(6);
            } else if (line === "" && eventName) {
              try {
                const data = JSON.parse(eventData) as Record<string, unknown>;
                onEvent({ event: eventName, data });

                if (eventName === "done" || eventName === "error") {
                  streamDone = true;
                  clearTimeout(timeout);
                  return;
                }
              } catch {
                // skip malformed JSON
              }
              eventName = "";
              eventData = "";
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        onError(err instanceof Error ? err : new Error("Stream error"));
      } finally {
        clearTimeout(timeout);
        if (!streamDone) onDone();
      }
    })
    .catch((err) => {
      clearTimeout(timeout);
      if (err instanceof DOMException && err.name === "AbortError") return;
      onError(err instanceof Error ? err : new Error("Connection error"));
      onDone();
    });

  return () => {
    clearTimeout(timeout);
    controller.abort();
  };
}

export async function sendChatMessage(
  messages: ChatMessage[],
  sessionId?: string,
): Promise<ChatResponse> {
  // Filter out tool messages and assistant-only tool-call placeholders.
  // Backend accepts only user/assistant text history — tool messages are
  // created server-side (CONTEXT.md § "Nội bộ AI Assistant").
  const sanitized: ChatRequestMessage[] = messages.flatMap((message) => {
    if (message.role === "tool") {
      return [];
    }

    if (message.content === null) {
      return [];
    }

    const content = message.content.trim();
    if (!content) {
      return [];
    }

    return [{ role: message.role, content }];
  });

  const body: Record<string, unknown> = { messages: sanitized };
  if (sessionId) {
    body.session_id = sessionId;
  }

  const res = await fetchWithTimeout(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`Assistant API ${res.status}: ${text}`);
  }

  return res.json() as Promise<ChatResponse>;
}

/**
 * Confirm a Draft Action by calling the real endpoint directly.
 * The LLM is never involved in the write — human-in-the-loop.
 * Contract: CONTEXT.md § "Nội bộ AI Assistant" (Draft Action).
 */
export async function confirmDraftAction(
  draft: DraftAction,
): Promise<unknown> {
  // SSRF guard: only allow endpoints under the configured API base.
  if (!draft.confirm_endpoint.startsWith("/api/")) {
    throw new Error("Invalid confirm endpoint: must start with /api/");
  }

  const res = await fetchWithTimeout(`${API_BASE_URL}${draft.confirm_endpoint}`, {
    method: draft.confirm_method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft.confirm_body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`Confirm API ${res.status}: ${text}`);
  }

  // Some write endpoints return 204 No Content.
  if (res.status === 204) {
    return undefined;
  }

  return res.json();
}

/**
 * Record the HR decision after the write (or rejection) has completed.
 */
export async function recordDraftDecision(
  draft: DraftAction,
  decision: "confirm" | "reject",
): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/draft-decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      decision,
      action_type: draft.action_type,
      provenance: draft.provenance ?? {},
      confirm_endpoint: draft.confirm_endpoint,
    }),
  });

    if (!res.ok) {
        throw new Error(`Assistant audit API ${res.status}`);
      }
    }

    /**
     * Send feedback (thumbs up/down) for an assistant message.
     *
     * @param feedback - Feedback details including session_id, message_index, feedback_type, and optional text.
     */
    export async function sendFeedback(
      feedback: AssistantFeedbackRequest,
    ): Promise<void> {
      const res = await fetchWithTimeout(`${BASE}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(feedback),
      });

    if (!res.ok) {
        const text = await res.text().catch(() => "Unknown error");
        throw new Error(`Feedback API ${res.status}: ${text}`);
      }
    }

/**
 * Start an AI Assistant chat session.
 * Called when ChatInterface mounts.
 */
export async function startSession(
  assistantType: "hr" | "employee",
): Promise<SessionStartResponse> {
  const res = await fetchWithTimeout(`${BASE}/session/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assistant_type: assistantType }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`Session API ${res.status}: ${text}`);
  }

  return res.json() as Promise<SessionStartResponse>;
}

/**
 * End an AI Assistant chat session.
 * Called when ChatInterface unmounts.
 */
export async function endSession(sessionId: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/session/end`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`Session API ${res.status}: ${text}`);
  }
}