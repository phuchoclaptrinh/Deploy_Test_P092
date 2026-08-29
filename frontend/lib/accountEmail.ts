const ACCOUNT_EMAIL_DOMAIN = "fixit.vn";

export function buildAccountEmailFromName(name: string) {
  const normalized = name
    .trim()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "d")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  const parts = normalized.split(/\s+/).filter(Boolean);
  const localPart = parts.length >= 2 ? `${parts.at(-1)}.${parts[0]}` : parts[0] || "";

  return localPart ? `${localPart}@${ACCOUNT_EMAIL_DOMAIN}` : "";
}
