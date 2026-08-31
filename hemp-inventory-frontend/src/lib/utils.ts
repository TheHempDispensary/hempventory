import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const ET = "America/New_York";

/**
 * Parse a timestamp coming from the API.
 *
 * SQLite's CURRENT_TIMESTAMP returns "2026-08-13 17:37:48" — UTC but with no
 * zone marker, which browsers parse as *local* time, shifting every displayed
 * time by the ET offset. Anything already carrying a zone is left alone.
 */
export function parseServerDate(value: string | number | Date): Date {
  if (value instanceof Date) return value;
  if (typeof value === "number") return new Date(value);
  const raw = value.trim().replace(" ", "T");
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(raw);
  return new Date(hasZone ? raw : `${raw}Z`);
}

/** A server timestamp as Eastern date + time, e.g. "8/13/2026, 1:37:48 PM". */
export function formatEtDateTime(value: string | number | Date): string {
  return parseServerDate(value).toLocaleString("en-US", { timeZone: ET });
}

/** A server timestamp as an Eastern date, e.g. "3/14/2026". */
export function formatEtDate(value: string | number | Date): string {
  return parseServerDate(value).toLocaleDateString("en-US", { timeZone: ET });
}

/** Today in Eastern time as "YYYY-MM-DD", for date inputs. */
export function etToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: ET });
}

/** A "YYYY-MM-DD" calendar date as "M/D/YYYY", without shifting the day. */
export function formatDateOnly(value: string): string {
  const m = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return value;
  return `${Number(m[2])}/${Number(m[3])}/${m[1]}`;
}

/**
 * Word-order-insensitive search: every whitespace-separated term in the query
 * must appear (as a substring) in at least one of the provided fields. So
 * "nerds smalls" matches "THC FLOWER SMALLS NERDS ..." just like "smalls nerds".
 */
export function matchesSearch(query: string, ...fields: (string | null | undefined)[]) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystacks = fields.map((f) => (f || "").toLowerCase());
  return terms.every((t) => haystacks.some((h) => h.includes(t)));
}
