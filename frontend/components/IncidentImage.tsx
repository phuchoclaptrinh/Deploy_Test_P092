import { Image as ImageIcon } from "lucide-react";
import type { TicketImage } from "@/lib/types";

export function IncidentImage({ image, alt, className = "" }: { image?: TicketImage; alt: string; className?: string }) {
  if (!image) return <div className={`imageEmpty ${className}`}><ImageIcon size={24} /><span>Không có ảnh</span></div>;
  const position = image.crop === "left" ? "left center" : image.crop === "right" ? "right center" : "center";
  return <img className={`incidentImage ${className}`} src={image.dataUrl} alt={alt} style={{ objectPosition: position }} />;
}

export function IncidentGallery({ images, image, alt, className = "" }: { images?: TicketImage[]; image?: TicketImage; alt: string; className?: string }) {
  const items = images?.length ? images : image ? [image] : [];
  if (items.length <= 1) return <IncidentImage image={items[0]} alt={alt} className={className} />;
  return <div className={`incidentGallery ${className}`}>{items.map((item, index) => <IncidentImage image={item} alt={`${alt} - ảnh ${index + 1}`} key={`${item.name}-${index}`} />)}</div>;
}
