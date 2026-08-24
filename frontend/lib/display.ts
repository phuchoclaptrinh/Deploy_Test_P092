/** The one reference code a report is known by.
 *  Must stay identical to the backend's `_display_code`: "PA-" plus the first
 *  six characters of the id, so a code read off a notification matches the one
 *  on the report itself and the one search accepts. */
export function formatTicketCode(id: string) {
  const value = id.trim();
  if (/^(?:TK|PA)-[A-Z0-9-]+$/i.test(value)) return value.toUpperCase();

  const compact = value.replace(/[^a-z0-9]/gi, "").toUpperCase();
  return `PA-${compact.slice(0, 6).padEnd(6, "0")}`;
}
