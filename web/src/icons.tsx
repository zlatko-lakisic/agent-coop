/** SVG kind icons for timeline cards. No letter squares. */

export type IconName = "message" | "progress" | "review" | "stall" | "file" | "role" | "gear";

export function iconForKind(kind: string): IconName {
  if (kind.includes("stall")) return "stall";
  if (kind.includes("review")) return "review";
  if (kind.includes("progress") || kind.includes("context")) return "progress";
  if (kind.includes("file")) return "file";
  if (kind.includes("membership") || kind.includes("join") || kind.includes("helper")) return "role";
  if (kind.includes("settings") || kind.includes("heartbeat") || kind.includes("round") || kind.includes("update") || kind.includes("trace") || kind.includes("grade"))
    return "gear";
  return "message";
}

export function KindIcon({ name, color }: { name: IconName; color: string }) {
  const common = { width: 14, height: 14, viewBox: "0 0 16 16", fill: "none", "aria-hidden": "true" as const };
  if (name === "stall") {
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="6" stroke={color} stroke-width="1.5" />
        <path d="M8 4.5v4l2.5 1.5" stroke={color} stroke-width="1.5" stroke-linecap="round" />
      </svg>
    );
  }
  if (name === "progress") {
    return (
      <svg {...common}>
        <path d="M3 12V4l5 3 5-3v8l-5-3-5 3z" stroke={color} stroke-width="1.5" stroke-linejoin="round" />
      </svg>
    );
  }
  if (name === "review") {
    return (
      <svg {...common}>
        <path d="M3 3.5h10v9H3z" stroke={color} stroke-width="1.5" />
        <path d="M5 7h6M5 9.5h4" stroke={color} stroke-width="1.5" stroke-linecap="round" />
      </svg>
    );
  }
  if (name === "file") {
    return (
      <svg {...common}>
        <path d="M5 2.5h4.5L12.5 5.5V13.5H5z" stroke={color} stroke-width="1.5" stroke-linejoin="round" />
        <path d="M9.5 2.5V5.5H12.5" stroke={color} stroke-width="1.5" />
      </svg>
    );
  }
  if (name === "role") {
    return (
      <svg {...common}>
        <circle cx="8" cy="5.5" r="2.2" stroke={color} stroke-width="1.5" />
        <path d="M3.5 13c.8-2.4 2.4-3.5 4.5-3.5s3.7 1.1 4.5 3.5" stroke={color} stroke-width="1.5" stroke-linecap="round" />
      </svg>
    );
  }
  if (name === "gear") {
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="2.2" stroke={color} stroke-width="1.5" />
        <path
          d="M8 2.5v1.4M8 12.1v1.4M2.5 8h1.4M12.1 8h1.4M4.1 4.1l1 1M10.9 10.9l1 1M11.9 4.1l-1 1M5.1 10.9l-1 1"
          stroke={color}
          stroke-width="1.5"
          stroke-linecap="round"
        />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M3 4.5h7.5L13 7v5.5H3z" stroke={color} stroke-width="1.5" stroke-linejoin="round" />
      <path d="M5 8h6M5 10.2h4" stroke={color} stroke-width="1.4" stroke-linecap="round" />
    </svg>
  );
}
