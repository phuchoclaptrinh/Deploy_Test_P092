import type { ReactNode } from "react";

type ManagerSurfaceProps = {
  title: string;
  description?: string;
  eyebrow?: string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
};

export function ManagerSurface({
  title,
  description,
  eyebrow,
  icon,
  actions,
  children,
  className = "",
  bodyClassName = "",
}: ManagerSurfaceProps) {
  return (
    <section className={`managerSurface${className ? ` ${className}` : ""}`}>
      <header className="managerSurfaceHeader">
        <div className="managerSurfaceHeading">
          {icon && <span className="managerSurfaceIcon">{icon}</span>}
          <div>
            {eyebrow && <span className="managerSurfaceEyebrow">{eyebrow}</span>}
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
        </div>
        {actions && <div className="managerSurfaceActions">{actions}</div>}
      </header>
      <div className={`managerSurfaceBody${bodyClassName ? ` ${bodyClassName}` : ""}`}>{children}</div>
    </section>
  );
}
