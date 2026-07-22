"use client";

import {
  forwardRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: ButtonVariant;
  busy?: boolean;
  busyLabel?: string;
  /** @deprecated Compatibility only; REALYTICS buttons have no app-specific behavior. */
  appName?: string;
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "border-transparent bg-re-action text-white hover:brightness-95",
  secondary: "border-re-border bg-re-surface text-re-text hover:bg-re-subtle",
  danger: "border-transparent bg-re-danger text-white hover:brightness-95",
  ghost: "border-transparent bg-transparent text-re-secondary hover:bg-re-subtle",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      children,
      className = "",
      variant = "secondary",
      busy = false,
      busyLabel = "처리 중",
      appName,
      disabled,
      type = "button",
      ...props
    },
    ref,
  ) {
    void appName;
    return (
      <button
        ref={ref}
        type={type}
        disabled={disabled || busy}
        aria-busy={busy || undefined}
        className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-re-control border px-4 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${className}`}
        {...props}
      >
        {busy ? busyLabel : children}
      </button>
    );
  },
);
