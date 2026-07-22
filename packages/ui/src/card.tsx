import { type HTMLAttributes, type JSX, type ReactNode } from "react";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

export function Card({
  className = "",
  title,
  description,
  actions,
  children,
  ...props
}: CardProps): JSX.Element {
  return (
    <section
      className={`rounded-re-panel border border-re-border bg-re-surface shadow-re-panel ${className}`}
      {...props}
    >
      {title || description || actions ? (
        <header className="flex min-h-14 items-start justify-between gap-4 border-b border-re-border px-4 py-3">
          <div>
            {title ? <h2 className="text-sm font-bold text-re-text">{title}</h2> : null}
            {description ? <p className="mt-1 text-xs leading-5 text-re-muted">{description}</p> : null}
          </div>
          {actions ? <div className="shrink-0">{actions}</div> : null}
        </header>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}
