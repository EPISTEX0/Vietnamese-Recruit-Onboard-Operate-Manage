    /**
     * API client for the Employee AI Assistant.
     *
     * Same interface as the HR assistant API client, but hits
     * POST /api/ess/assistant/chat instead of /api/assistant/chat.
     *
     * Conversation history is held in React state (not persisted).
     * Each request sends the full history; backend processes statelessly.
     * employee_id is always taken from the session on the backend — never from the LLM.
     */

        import { API_BASE_URL } from "./client";
        import { ApiError } from "./types";

    const BASE = `${API_BASE_URL}/api/ess/assistant`;
    const TIMEOUT_MS = 60_000; // LLM calls can be slow

    // ---------------------------------------------------------------------------
    // Types (re-exported from HR assistant for shared shape)
    // ---------------------------------------------------------------------------

    export type {
      ChatMessage,
      DraftAction,
      ChatResponse,
      SessionStartResponse,
      AssistantFeedbackRequest,
    } from "./assistant";

    import type {
      ChatMessage,
      DraftAction,
      ChatResponse,
      SessionStartResponse,
      AssistantFeedbackRequest,
    } from "./assistant";

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
            return await fetch(url, {
              ...init,
              signal: controller.signal,
              credentials: "include",
            });
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
     * Send a chat message to the Employee AI Assistant.
     *
     * @param messages - Full conversation history. Last message must be from user.
     * @param sessionId - Optional session UUID for latency tracking.
     * @returns New messages from this turn + optional Draft Action.
     */
    export async function sendEmployeeChatMessage(
      messages: ChatMessage[],
      sessionId?: string,
    ): Promise<ChatResponse> {
      // Client-side guard: last message must be from user
      const lastMsg = messages[messages.length - 1];
      if (lastMsg && lastMsg.role !== "user") {
        throw new Error("Last message must be from user");
      }
      // Filter out tool messages and assistant-only tool-call placeholders.
      // Backend accepts only user/assistant text history — tool messages are
      // created server-side (CONTEXT.md § "Nội bộ AI Assistant").
      const sanitized: { role: "user" | "assistant"; content: string }[] =
        messages.flatMap((message) => {
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

      const res = await fetchWithTimeout(`${BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => "Unknown error");
        throw new Error(`Employee Assistant API ${res.status}: ${text}`);
      }

      return res.json() as Promise<ChatResponse>;
    }

    /**
     * Send a chat message to the Employee AI Assistant via SSE streaming.
     *
     * Opens a POST connection to /api/ess/assistant/chat/stream and reads
     * Server-Sent Events. Calls onEvent for each event (text_delta,
     * tool_start, tool_end, draft_action, done, error).
     *
     * Returns an abort function to cancel the stream.
     */
    export function sendEmployeeStreamMessage(
      messages: ChatMessage[],
      onEvent: (event: { event: string; data: Record<string, unknown> }) => void,
      onError: (error: Error) => void,
      onDone: () => void,
      sessionId?: string,
    ): () => void {
      const sanitized: { role: "user" | "assistant"; content: string }[] =
        messages.flatMap((message) => {
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
            onError(new Error(`Employee Assistant API ${res.status}: ${text}`));
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

    /**
     * Confirm a Draft Action by calling the real endpoint directly.
     * Strictly scoped to employee endpoints — rejects HR admin endpoints.
     * This prevents employee assistant from accidentally calling HR APIs.
     */
    export async function confirmEmployeeDraftAction(
      draft: DraftAction,
    ): Promise<unknown> {
      // Scoped guard: only allow /api/employee-requests/me/* endpoints
      if (!draft.confirm_endpoint.startsWith("/api/employee-requests/me/")) {
        throw new Error(
          "Invalid confirm endpoint: must start with /api/employee-requests/me/",
        );
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

      if (res.status === 204) {
        return undefined;
      }

      return res.json();
    }

    /**
     * Send employee feedback (thumbs up/down) for an assistant message.
     */
    export async function sendEmployeeFeedback(
      feedback: AssistantFeedbackRequest,
    ): Promise<void> {
      const res = await fetchWithTimeout(`${BASE}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(feedback),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => "Unknown error");
        throw new Error(`Employee Feedback API ${res.status}: ${text}`);
      }
    }

    /**
     * Start an Employee AI Assistant chat session.
     * Called when ChatInterface mounts.
     */
    export async function startEmployeeSession(): Promise<SessionStartResponse> {
      const res = await fetchWithTimeout(`${BASE}/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ assistant_type: "employee" }),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => "Unknown error");
        throw new Error(`Employee Session API ${res.status}: ${text}`);
      }

      return res.json() as Promise<SessionStartResponse>;
    }

    /**
     * End an Employee AI Assistant chat session.
     * Called when ChatInterface unmounts.
     */
    export async function endEmployeeSession(sessionId: string): Promise<void> {
      const res = await fetchWithTimeout(`${BASE}/session/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => "Unknown error");
        throw new Error(`Employee Session API ${res.status}: ${text}`);
      }
    }
