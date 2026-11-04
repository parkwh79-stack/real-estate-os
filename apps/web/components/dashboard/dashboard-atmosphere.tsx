"use client";

import { useEffect, useRef } from "react";

/** Decorative only: project data and controls never move with the scroll. */
export function DashboardAtmosphere() {
  const decoration = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = decoration.current;
    if (!element) return;
    const motion = window.matchMedia("(min-width: 768px) and (prefers-reduced-motion: no-preference)");
    let frame: number | null = null;
    const update = () => {
      frame = null;
      const offset = Math.round(Math.max(0, Math.min(window.scrollY, 300)) * 0.06);
      element.style.setProperty("--dashboard-parallax-y", `${offset}px`);
    };
    const onScroll = () => {
      if (frame === null) frame = window.requestAnimationFrame(update);
    };
    const syncMotion = () => {
      window.removeEventListener("scroll", onScroll);
      if (frame !== null) { window.cancelAnimationFrame(frame); frame = null; }
      if (motion.matches) {
        update();
        window.addEventListener("scroll", onScroll, { passive: true });
      } else {
        element.style.setProperty("--dashboard-parallax-y", "0px");
      }
    };
    syncMotion();
    motion.addEventListener("change", syncMotion);
    return () => {
      window.removeEventListener("scroll", onScroll);
      motion.removeEventListener("change", syncMotion);
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, []);

  return <div ref={decoration} className="re-dashboard-atmosphere" data-testid="dashboard-atmosphere" aria-hidden="true" />;
}
