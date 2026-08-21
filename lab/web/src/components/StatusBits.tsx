import type { Status } from "../types";

export const STATUS_COLOR: Record<Status, string> = {
  verified: "var(--ok)",
  low_confidence: "var(--warn)",
  ambiguous: "var(--amb)",
  not_found: "var(--bad)",
  not_present: "var(--np)",
};

export const STATUS_TAG: Record<Status, string> = {
  verified: "VERIFIED",
  low_confidence: "LOW CONF",
  ambiguous: "AMBIGUOUS",
  not_found: "NOT FOUND",
  not_present: "NULL",
};

export function StatusDot({ status }: { status: Status }) {
  return (
    <span
      className="mt-1.5 inline-block h-2 w-2 flex-none rounded-full"
      style={{ background: STATUS_COLOR[status] }}
    />
  );
}

export function StatusTag({ status }: { status: Status }) {
  return (
    <span
      className="self-center rounded px-1.5 py-px text-[10px] tracking-wide"
      style={{ color: STATUS_COLOR[status], border: `1px solid ${STATUS_COLOR[status]}55` }}
    >
      {STATUS_TAG[status]}
    </span>
  );
}
