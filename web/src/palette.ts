export type ThemeName = "dark" | "light";

export type Palette = {
  page: string;
  surface: string;
  surface2: string;
  border: string;
  text: string;
  muted: string;
  driver: string;
  teal: string;
  blue: string;
  amber: string;
  problem: string;
  problemBg: string;
  attention: string;
  attentionBg: string;
  review: string;
  system: string;
  accent: string;
  accentBg: string;
};

export function palette(dark: boolean): Palette {
  if (dark) {
    return {
      page: "#12111C",
      surface: "#1A1928",
      surface2: "#211F31",
      border: "#2E2B46",
      text: "#ECEAF6",
      muted: "#A9A6C2",
      driver: "#9A9AF5",
      teal: "#3CC4B4",
      blue: "#6AAAF2",
      amber: "#F2A33A",
      problem: "#F37878",
      problemBg: "rgba(243,120,120,0.12)",
      attention: "#F2B451",
      attentionBg: "rgba(242,180,81,0.12)",
      review: "#B39AF5",
      system: "#C9C6DD",
      accent: "#F2A33A",
      accentBg: "rgba(242,163,58,0.08)",
    };
  }
  return {
    page: "#F6F4F0",
    surface: "#FFFFFF",
    surface2: "#FBFAF7",
    border: "#E4E0D8",
    text: "#1E1B4B",
    muted: "#5B5878",
    driver: "#3A3A8F",
    teal: "#13786E",
    blue: "#2366B0",
    amber: "#A85F0E",
    problem: "#C23535",
    problemBg: "rgba(194,53,53,0.08)",
    attention: "#9A6008",
    attentionBg: "rgba(242,163,58,0.14)",
    review: "#6A4BC4",
    system: "#4A4766",
    accent: "#E08A1E",
    accentBg: "rgba(242,163,58,0.10)",
  };
}

export function agentColor(c: Palette, name: string): string {
  const key = name.toLowerCase();
  if (key.includes("cursor") || key === "driver") return c.driver;
  if (key.includes("claude")) return c.teal;
  if (key.includes("codex")) return c.blue;
  if (key.includes("gemini")) return c.amber;
  return c.system;
}

export function agentRadius(name: string, role?: string): string {
  if (role === "driver" || name.toLowerCase() === "cursor") return "6px";
  return "50%";
}
