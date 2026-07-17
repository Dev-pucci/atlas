import "server-only";

/**
 * TypeScript port of annotator/pipeline/client.py's calling convention: OpenAI-compatible
 * chat completions against OpenRouter, with code-fence-tolerant JSON parsing and a
 * retry-once-on-parse-failure, matching Router.chat_json / parse_json_reply.
 */

const BASE_URL = "https://openrouter.ai/api/v1";

export type ChatContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string | ChatContentPart[];
}

export function textPart(text: string): ChatContentPart {
  return { type: "text", text };
}

export function imagePart(jpegBase64: string): ChatContentPart {
  return { type: "image_url", image_url: { url: `data:image/jpeg;base64,${jpegBase64}` } };
}

function apiKey(): string {
  const key = process.env.OPENROUTER_API_KEY;
  if (!key) throw new Error("Missing OPENROUTER_API_KEY environment variable");
  return key;
}

async function chatOnce(model: string, messages: ChatMessage[], maxTokens = 3000): Promise<string> {
  const resp = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey()}`,
    },
    body: JSON.stringify({ model, messages, max_tokens: maxTokens }),
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`OpenRouter ${resp.status}: ${body.slice(0, 500)}`);
  }
  const data = await resp.json();
  const choice = data.choices?.[0];
  const text: string = choice?.message?.content ?? "";
  if (!text.trim()) {
    throw new Error(`Empty response from ${model} (finish=${choice?.finish_reason})`);
  }
  if (choice?.finish_reason === "length") {
    throw new Error(`Truncated response from ${model} (hit max_tokens)`);
  }
  return text;
}

/** Extract JSON from a model reply that may include prose or code fences. */
export function parseJsonReply(text: string): unknown {
  let candidate = text.trim();
  const fence = candidate.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) candidate = fence[1].trim();
  try {
    return JSON.parse(candidate);
  } catch {
    // fall back: widest {...} or [...] span
    for (const [open, close] of [
      ["{", "}"],
      ["[", "]"],
    ] as const) {
      const start = candidate.indexOf(open);
      const end = candidate.lastIndexOf(close);
      if (start !== -1 && end > start) {
        try {
          return JSON.parse(candidate.slice(start, end + 1));
        } catch {
          continue;
        }
      }
    }
    throw new Error(`Could not parse JSON from model reply:\n${text.slice(0, 800)}`);
  }
}

/** Chat expecting a JSON object/array in the reply; retries once on parse failure. */
export async function chatJson(
  model: string,
  messages: ChatMessage[],
  maxTokens = 3000
): Promise<Record<string, unknown>> {
  const text = await chatOnce(model, messages, maxTokens);
  try {
    return parseJsonReply(text) as Record<string, unknown>;
  } catch {
    const retryText = await chatOnce(model, messages, maxTokens);
    return parseJsonReply(retryText) as Record<string, unknown>;
  }
}
