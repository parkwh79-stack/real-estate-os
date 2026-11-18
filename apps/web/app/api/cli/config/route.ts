import { NextResponse } from "next/server";
import { cliPublicConfig } from "../../../../lib/cli-public-config";

export const dynamic = "force-dynamic";

export async function GET() {
  const config = cliPublicConfig({
    REALYTICS_API_ORIGIN: process.env.REALYTICS_API_ORIGIN,
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  });
  return NextResponse.json(config ?? { error: "cli_public_configuration_unavailable" }, {
    status: config ? 200 : 503,
    headers: {
      "Cache-Control": "private, no-store",
      Pragma: "no-cache",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
