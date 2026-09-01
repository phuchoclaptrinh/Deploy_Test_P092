import { ArrowUpRight } from "lucide-react";

type ManagerStatCardProps = {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  description: string;
  tone: "primary" | "green" | "danger" | "neutral";
  active?: boolean;
  onClick?: () => void;
};

export function ManagerStatCard({ icon, label, value, description, tone, active = false, onClick }: ManagerStatCardProps) {
  const content = <>
    <span className="managerStatHeader"><span>{label}</span><span className="managerStatIcon">{icon}<ArrowUpRight size={14} /></span></span>
    <strong>{value}</strong>
    <small>{description}</small>
  </>;

  if (onClick) return <button type="button" className={`managerStatCard tone-${tone}${active ? " active" : ""}`} aria-pressed={active} onClick={onClick}>{content}</button>;
  return <article className={`managerStatCard tone-${tone}`}>{content}</article>;
}
