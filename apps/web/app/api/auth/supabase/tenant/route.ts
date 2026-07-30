import { NextRequest, NextResponse } from "next/server";
import {
  clearTenantSelectorCookie,
  normalizedTenantSelector,
  preventAuthenticatedResponseCaching,
  realyticsAcceptsBearer,
  requestIsSameOrigin,
  setTenantSelectorCookie,
} from "../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return authJson({ error: "cross_site_request_rejected" }, 403);
  }

  const payload = await request.json().catch(() => null) as {
    tenantId?: unknown;
  } | null;
  const tenantId = normalizedTenantSelector(payload?.tenantId);
  if (!tenantId) {
    return authJson({ error: "invalid_tenant_selector" }, 400);
  }

  try {
    const supabase = await createSupabaseServerClient();
    const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !claimsData?.claims) {
      return authJson({ error: "authentication_required" }, 401);
    }

    // getSession is used only after getClaims has verified the cookie-backed
    // identity. The selected Tenant is never accepted from the browser until
    // FastAPI confirms that the same bearer has an active membership in it.
    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    if (!accessToken) {
      return authJson({ error: "authentication_required" }, 401);
    }

    const identity = await realyticsAcceptsBearer(accessToken, tenantId);
    if (identity.status === "accepted") {
      const response = authJson({ selected: true, tenantId });
      setTenantSelectorCookie(response, tenantId);
      return response;
    }
    if (
      identity.status === "identity_authority_unavailable"
      || identity.status === "backend_unavailable"
    ) {
      return authJson({ error: "identity_authority_unavailable" }, 503);
    }
    return authJson({ error: "tenant_selection_not_authorized" }, 403);
  } catch {
    return authJson({ error: "authentication_unavailable" }, 503);
  }
}

export async function DELETE(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return authJson({ error: "cross_site_request_rejected" }, 403);
  }
  const response = authJson({ selected: false });
  clearTenantSelectorCookie(response);
  return response;
}

function authJson(body: Record<string, unknown>, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}
