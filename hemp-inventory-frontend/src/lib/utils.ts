import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
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
