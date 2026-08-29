"use client";

import { useCallback, useEffect, useState } from "react";
import { getCurrentUser } from "@/api/auth.api";
import { readResidentMockUser, residentMockOtpEnabled } from "@/lib/residentMockSession";
import type { CurrentUser } from "@/types/api";

/** The shell and several screens need the same profile. One shared fetch keeps
 *  the apartment-link check (C-01) consistent and avoids duplicate requests. */
let cachedUser: CurrentUser | null = null;
let pending: Promise<CurrentUser> | null = null;

function loadProfile() {
  if (residentMockOtpEnabled) return Promise.resolve(readResidentMockUser());
  if (cachedUser) return Promise.resolve(cachedUser);
  if (!pending) {
    pending = getCurrentUser()
      .then((user) => { cachedUser = user; return user; })
      .finally(() => { pending = null; });
  }
  return pending;
}

export function clearResidentProfileCache() {
  cachedUser = null;
}

export type ResidentProfileState = {
  user: CurrentUser | null;
  loading: boolean;
  error: string;
  reload: () => void;
};

export function useResidentProfile(): ResidentProfileState {
  const [user, setUser] = useState<CurrentUser | null>(cachedUser);
  const [loading, setLoading] = useState(!cachedUser);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadProfile()
      .then((value) => { if (active) { setUser(value); setError(""); } })
      .catch(() => { if (active) setError("Không tải được thông tin tài khoản."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt]);

  const reload = useCallback(() => { clearResidentProfileCache(); setAttempt((value) => value + 1); }, []);
  return { user, loading, error, reload };
}
