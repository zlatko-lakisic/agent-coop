/** Simple platform marks for agents the board knows. Fallback is the letter chip. */

export type AgentIconProps = {
  tool?: string;
  name?: string;
  color: string;
  size?: number;
};

function normalizeTool(tool?: string, name?: string): string {
  const raw = (tool || name || "").toLowerCase().replace(/_/g, "-");
  if (!raw) return "";
  if (raw.includes("claude")) return raw.includes("desktop") ? "claude-desktop" : raw.includes("web") ? "claude-web" : "claude-code";
  if (raw.includes("chatgpt") || raw.includes("codex")) {
    if (raw.includes("work")) return "chatgpt-work";
    if (raw.includes("web")) return "chatgpt-web";
    if (raw.includes("chatgpt-codex") || raw === "chatgpt-codex") return "chatgpt-codex";
    return raw.includes("codex") && !raw.includes("chatgpt") ? "codex" : "chatgpt-codex";
  }
  if (raw.includes("gemini")) return "gemini-cli";
  if (raw.includes("copilot") || raw.includes("github")) return "copilot";
  if (raw.includes("opencode")) return "opencode";
  if (raw.includes("cline")) return "cline";
  if (raw.includes("goose")) return "goose";
  if (raw.includes("ollama")) return "ollama";
  if (raw.includes("lmstudio") || raw.includes("lm-studio")) return "lmstudio";
  if (raw.includes("runner")) return "runner";
  if (raw.includes("cursor")) return "cursor";
  return raw;
}

export function AgentIcon({ tool, name, color, size = 28 }: AgentIconProps) {
  const key = normalizeTool(tool, name);
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 28 28",
    "aria-hidden": "true" as const,
  };
  const letter = (name || tool || "?").slice(0, 1).toUpperCase();

  if (key === "cursor") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill={color} />
        <path d="M8 7.5 19.5 14 12.2 15.6 10.2 21.2z" fill="#fff" />
      </svg>
    );
  }
  if (key === "claude-code" || key === "claude-desktop" || key === "claude-web") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#D97757" />
        <path d="M8 18.5 11.2 9h2.4L17 18.5h-2.3l-.6-1.8H11l-.6 1.8zm3.4-3.5h2.2l-1.1-3.2z" fill="#fff" />
      </svg>
    );
  }
  if (key === "codex" || key === "chatgpt-codex" || key === "chatgpt-work" || key === "chatgpt-web") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#10A37F" />
        <circle cx="14" cy="14" r="6.2" stroke="#fff" stroke-width="1.6" fill="none" />
        <circle cx="14" cy="14" r="2.2" fill="#fff" />
      </svg>
    );
  }
  if (key === "gemini-cli") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#4285F4" />
        <path d="M14 6.5c2.2 3.2 2.2 11.8 0 15-2.2-3.2-2.2-11.8 0-15z" fill="#fff" opacity="0.95" />
        <path d="M6.5 14c3.2-2.2 11.8-2.2 15 0-3.2 2.2-11.8 2.2-15 0z" fill="#fff" opacity="0.7" />
      </svg>
    );
  }
  if (key === "copilot") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#24292F" />
        <path d="M9 18.5c0-2.4 1.6-3.6 3.4-4.1C10.8 13.7 10 12.4 10 10.8 10 8.6 11.6 7 14 7s4 1.6 4 3.8c0 1.6-.8 2.9-2.4 3.6 1.8.5 3.4 1.7 3.4 4.1v.8H9z" fill="#fff" />
        <circle cx="11.6" cy="12.2" r="1" fill="#24292F" />
        <circle cx="16.4" cy="12.2" r="1" fill="#24292F" />
      </svg>
    );
  }
  if (key === "opencode") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#111827" />
        <path d="M8 9h4l2 4 2-4h4l-4 7.5V21h-4v-4.5z" fill="#FBBF24" />
      </svg>
    );
  }
  if (key === "cline") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#6366F1" />
        <path d="M8 8.5h5.5a4 4 0 010 8H11V21H8zm3 5.5h2.4a1.5 1.5 0 000-3H11z" fill="#fff" />
      </svg>
    );
  }
  if (key === "goose") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#0F766E" />
        <ellipse cx="14" cy="15" rx="6" ry="5" fill="#fff" />
        <circle cx="16.5" cy="13.5" r="1.1" fill="#0F766E" />
        <path d="M19.5 14.5 23 13.2 19.7 16z" fill="#F59E0B" />
      </svg>
    );
  }
  if (key === "ollama" || key === "runner") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#111827" />
        <ellipse cx="14" cy="15.5" rx="7" ry="5.5" fill="#fff" />
        <circle cx="11.2" cy="14.8" r="1.2" fill="#111827" />
        <circle cx="16.8" cy="14.8" r="1.2" fill="#111827" />
        <path d="M11.5 18c1.5 1 3.5 1 5 0" stroke="#111827" stroke-width="1.3" stroke-linecap="round" fill="none" />
      </svg>
    );
  }
  if (key === "lmstudio") {
    return (
      <svg {...common}>
        <rect width="28" height="28" rx="8" fill="#7C3AED" />
        <path d="M8 19V9h2.4l3.1 6.2L16.6 9H19v10h-2.2v-5.6L14.2 19h-1.4l-2.6-5.6V19z" fill="#fff" />
      </svg>
    );
  }

  return (
    <span
      style={{
        width: size,
        height: size,
        borderRadius: 8,
        background: color,
        color: "#fff",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 700,
        fontSize: Math.max(11, size * 0.42),
        flex: "0 0 auto",
      }}
    >
      {letter}
    </span>
  );
}
