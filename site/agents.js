// status: "lead" | "help" | "soon" | "cannot"
// TODO(zlatko): confirm that showing each brand's logo is acceptable
// under that brand's guidelines before launch. Set icon to null to use
// the monogram.

export const ROLE = {
  lead: "can lead",
  help: "can help",
  soon: "coming soon",
  cannot: "can't join",
};

export const agents = [
  { id: "cursor", name: "Cursor", status: "lead", ringPriority: 1, icon: "icons/cursor.svg", tint: "#EEF0F3", brand: "#000000" },
  { id: "claude-code", name: "Claude Code", status: "lead", ringPriority: 2, icon: "icons/claude.svg", tint: "#F6EDE6", brand: "#D97757" },
  { id: "codex", name: "Codex", status: "lead", ringPriority: 3, icon: null, tint: "#ECEFEE" },
  { id: "chatgpt-desktop", name: "ChatGPT desktop app", status: "lead", ringPriority: 4, icon: null, tint: "#E9F3EF" },
  { id: "gemini-cli", name: "Gemini CLI", status: "lead", ringPriority: 5, icon: "icons/gemini.svg", tint: "#EAF0FB", brand: "#8E75B2" },
  { id: "ollama", name: "Ollama", status: "help", ringPriority: 6, icon: "icons/ollama.svg", tint: "#F1F1F1", brand: "#000000" },
  { id: "lm-studio", name: "LM Studio", status: "help", ringPriority: 7, icon: "icons/lmstudio.svg", tint: "#EFEAF8", brand: "#000000" },
  { id: "github-copilot", name: "GitHub Copilot", status: "soon", icon: "icons/githubcopilot.svg", tint: "#EEF0F3", brand: "#000000" },
  { id: "opencode", name: "OpenCode", status: "soon", icon: "icons/opencode.svg", tint: "#F1F1F1", brand: "#000000" },
  { id: "cline", name: "Cline", status: "soon", icon: "icons/cline.svg", tint: "#EEF3F8", brand: "#7B3FF2" },
  { id: "goose", name: "Goose", status: "soon", icon: null, tint: "#F4F1EA" },
  { id: "claude-desktop", name: "Claude desktop chat", status: "soon", icon: "icons/claude.svg", tint: "#F6EDE6", brand: "#D97757" },
  { id: "chatgpt-work", name: "ChatGPT Work", status: "soon", icon: null, tint: "#E9F3EF" },
  { id: "web-chat", name: "Web chat", status: "cannot", icon: "icons/browser.svg", tint: "#F3EEE3" },
];

export function initials(name) {
  const p = name.split(/[\s-]+/).filter(Boolean);
  return p.length === 1 ? p[0].slice(0, 2).toUpperCase() : (p[0][0] + p[1][0]).toUpperCase();
}

export function byStatus(status) {
  return agents.filter((a) => a.status === status);
}

export function ringAgents(limit) {
  const list = agents.filter((a) => a.status === "lead" || a.status === "help").sort((a, b) => a.ringPriority - b.ringPriority);
  return typeof limit === "number" ? list.slice(0, limit) : list;
}

export function worksWithText() {
  const names = ringAgents().map((a) => (a.id === "chatgpt-desktop" ? "the ChatGPT desktop app" : a.name));
  const last = names.pop();
  return "Works with " + names.join(", ") + " and " + last + ".";
}
