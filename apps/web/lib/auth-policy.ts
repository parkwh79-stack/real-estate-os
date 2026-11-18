export function localPilotAuthEnabled(): boolean {
  return (
    process.env.NODE_ENV !== "production"
    && process.env.NEXT_PUBLIC_REALYTICS_ENABLE_LOCAL_AUTH === "true"
  );
}
