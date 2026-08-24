"use client";

import { useSyncExternalStore } from "react";
import { getServerSnapshot, getStoreSnapshot, subscribeStore } from "./mockService";

export function useStoreRevision() {
  return useSyncExternalStore(subscribeStore, getStoreSnapshot, getServerSnapshot);
}
