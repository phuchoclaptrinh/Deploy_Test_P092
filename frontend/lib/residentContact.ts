/** Building Management contact details come from project configuration.
 *  When no number is configured the call action is hidden rather than shown
 *  with a placeholder (docs/ui/UX_FLOWS.md section 6). */
export const buildingManagementPhone = (process.env.NEXT_PUBLIC_BUILDING_MANAGEMENT_PHONE || "").trim();

export function formatPhoneForDisplay(phone: string) {
  const digits = phone.replace(/[^\d+]/g, "");
  if (/^0\d{9}$/.test(digits)) return `${digits.slice(0, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
  return phone;
}
