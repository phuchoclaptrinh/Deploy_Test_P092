const KEY = "fixit_manager_seen_tickets";

export function getSeenManagerTickets() {
  if (typeof window === "undefined") return new Set<string>();
  try { return new Set<string>(JSON.parse(localStorage.getItem(KEY) || "[]")); }
  catch { return new Set<string>(); }
}

export function markManagerTicketSeen(id: string) {
  const seen = getSeenManagerTickets();
  seen.add(id);
  localStorage.setItem(KEY, JSON.stringify([...seen]));
  window.dispatchEvent(new Event("manager-ticket-seen"));
}
