import { redirect } from "next/navigation";

/**
 * The auto-assignment proposal flow now lives inside the dashboard dispatch
 * workspace. This route stays so existing bookmarks keep working, and sends
 * them straight to the workspace instead of a page that no longer exists.
 */
export default function ManagerAutomationRedirect() {
  redirect("/manager?view=assignment");
}
