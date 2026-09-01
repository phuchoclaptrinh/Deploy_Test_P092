import type { TicketImage } from "@/lib/types";
import type { ResidentTicket } from "@/types/api";

type ResidentTicketPrefetch = {
  ticket: ResidentTicket;
  images: TicketImage[];
};

const residentTicketPrefetch = new Map<string, ResidentTicketPrefetch>();

export function cacheResidentTicketPrefetch(id: string, value: ResidentTicketPrefetch) {
  residentTicketPrefetch.set(id, value);
}

export function getResidentTicketPrefetch(id: string) {
  return residentTicketPrefetch.get(id);
}
