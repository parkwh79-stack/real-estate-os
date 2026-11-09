import { NextRequest, NextResponse } from "next/server";
import {
  preventAuthenticatedResponseCaching,
  requestIsSameOrigin,
} from "../../../../../lib/backend-session";
import { evaluateHighRiskAuthOperationalGate } from "../../../../../lib/supabase/auth-operational-gate";
import { evaluateIdentityUnlink } from "../../../../../lib/supabase/identity-security";
import {
  evaluateRecentAal2,
  evaluateTotpEnrollmentEligibility,
  findVerifiedTotpFactor,
  parseTotpChallengeInput,
} from "../../../../../lib/supabase/mfa-security";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";
import { verifyCurrentSupabaseSession } from "../../../../../lib/supabase/verified-session";

type SecurityAction = {
  action?: unknown;
  factorId?: unknown;
  code?: unknown;
  friendlyName?: unknown;
  identityId?: unknown;
};

const HIGH_RISK_IDENTITY_PROVIDERS = new Set(["google", "kakao"]);

function json(body: unknown, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}

function safeIdentifier(value: unknown): string {
  const text = typeof value === "string" ? value.trim() : "";
  return /^[A-Za-z0-9._:-]{1,160}$/.test(text) ? text : "";
}

export async function GET(request: NextRequest) {
  const requestedProvider = request.nextUrl.searchParams.get("provider")?.trim()
    .toLowerCase() ?? "";
  if (requestedProvider && !HIGH_RISK_IDENTITY_PROVIDERS.has(requestedProvider)) {
    return json({ error: "unsupported_identity_provider" }, 400);
  }
  try {
    const supabase = await createSupabaseServerClient();
    const verifiedSession = await verifyCurrentSupabaseSession(supabase);
    if (!verifiedSession.verified) {
      return json({ error: verifiedSession.error }, verifiedSession.status);
    }
    const [identityResult, factorResult, aalResult] = await Promise.all([
      supabase.auth.getUserIdentities(),
      supabase.auth.mfa.listFactors(),
      supabase.auth.mfa.getAuthenticatorAssuranceLevel(),
    ]);
    if (identityResult.error || factorResult.error || aalResult.error) {
      return json({ error: "security_profile_unavailable" }, 503);
    }
    const identities = (identityResult.data?.identities ?? []).map((identity) => ({
      identityId: identity.identity_id,
      provider: identity.provider,
      email: typeof identity.identity_data?.email === "string"
        ? identity.identity_data.email
        : null,
      createdAt: identity.created_at ?? null,
      lastSignInAt: identity.last_sign_in_at ?? null,
    }));
    // `totp` and `phone` contain verified factors only. Keep `all` here so an
    // interrupted enrollment remains visible and can be explicitly removed
    // before the user starts over.
    const allFactors = factorResult.data?.all ?? [
      ...(factorResult.data?.totp ?? []),
      ...(factorResult.data?.phone ?? []),
    ];
    const factors = allFactors.map((factor) => ({
      factorId: factor.id,
      factorType: factor.factor_type,
      status: factor.status,
      friendlyName: factor.friendly_name ?? null,
      createdAt: factor.created_at,
      updatedAt: factor.updated_at,
    }));
    const operationalGate = evaluateHighRiskAuthOperationalGate({
      userId: verifiedSession.user.id,
      requestedProvider,
      identities,
      factors,
      currentLevel: aalResult.data.currentLevel,
    });
    const totpEnrollment = evaluateTotpEnrollmentEligibility(
      verifiedSession.user,
    );
    const recentAal2 = evaluateRecentAal2(
      aalResult.data.currentLevel,
      aalResult.data.currentAuthenticationMethods,
    );
    const limitations = Array.from(new Set([
      ...operationalGate.limitations,
      ...(recentAal2.limitation ? [recentAal2.limitation] : []),
    ]));
    return json({
      schemaVersion: "realytics-auth-security-profile-v1",
      userId: verifiedSession.user.id,
      requestedProvider: requestedProvider || null,
      identities,
      factors,
      assurance: {
        currentLevel: aalResult.data.currentLevel,
        nextLevel: aalResult.data.nextLevel,
        currentAuthenticationMethods: aalResult.data.currentAuthenticationMethods,
        recentAal2,
      },
      totpEnrollment,
      operationalForHighRiskActions: operationalGate.operational && recentAal2.eligible,
      limitations,
    });
  } catch {
    return json({ error: "supabase_auth_not_configured" }, 503);
  }
}

export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return json({ error: "cross_site_request_rejected" }, 403);
  }
  const payload = await request.json().catch(() => null) as SecurityAction | null;
  const action = typeof payload?.action === "string" ? payload.action : "";

  try {
    const supabase = await createSupabaseServerClient();
    const verifiedSession = await verifyCurrentSupabaseSession(supabase);
    if (!verifiedSession.verified) {
      return json({ error: verifiedSession.error }, verifiedSession.status);
    }

    if (action === "mfa_enroll_totp") {
      const eligibility = evaluateTotpEnrollmentEligibility(verifiedSession.user);
      if (!eligibility.eligible) {
        return json({ error: eligibility.limitation }, 409);
      }
      const friendlyName = typeof payload?.friendlyName === "string"
        ? payload.friendlyName.trim().slice(0, 80)
        : "REALYTICS Authenticator";
      const { data, error } = await supabase.auth.mfa.enroll({
        factorType: "totp",
        friendlyName: friendlyName || "REALYTICS Authenticator",
      });
      if (error) return json({ error: "mfa_enrollment_failed" }, 400);
      return json({
        schemaVersion: "realytics-mfa-enrollment-v1",
        factorId: data.id,
        factorType: data.type,
        friendlyName: data.friendly_name ?? null,
        // Supabase may return an SVG data URI with a trailing newline. Next.js
        // rejects data-image sources that end in whitespace, so normalize the
        // transport value before it reaches any renderer.
        qrCode: data.totp.qr_code.trimEnd(),
        secret: data.totp.secret,
        uri: data.totp.uri,
      });
    }

    if (action === "mfa_verify_totp") {
      const challenge = parseTotpChallengeInput({
        factorId: payload?.factorId,
        code: payload?.code,
      });
      if (!challenge) {
        return json({ error: "invalid_mfa_verification_input" }, 400);
      }
      const { error } = await supabase.auth.mfa.challengeAndVerify(challenge);
      if (error) return json({ error: "mfa_verification_failed" }, 400);
      return json({ verified: true, assuranceLevel: "aal2" });
    }

    if (action === "mfa_reauthenticate_totp") {
      const challenge = parseTotpChallengeInput({
        factorId: payload?.factorId,
        code: payload?.code,
      });
      if (!challenge) {
        return json({ error: "invalid_mfa_reauthentication_input" }, 400);
      }
      const factorsResult = await supabase.auth.mfa.listFactors();
      if (factorsResult.error) {
        return json({ error: "verified_totp_factor_unavailable" }, 503);
      }
      const verifiedFactor = findVerifiedTotpFactor(
        factorsResult.data?.totp ?? [],
        challenge.factorId,
      );
      if (!verifiedFactor) {
        return json({ error: "verified_totp_factor_required" }, 409);
      }
      const { error } = await supabase.auth.mfa.challengeAndVerify(challenge);
      if (error) return json({ error: "mfa_reauthentication_failed" }, 400);

      const assuranceResult = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
      if (assuranceResult.error || assuranceResult.data.currentLevel !== "aal2") {
        return json({ error: "mfa_reauthentication_incomplete" }, 503);
      }
      return json({
        schemaVersion: "realytics-mfa-reauthentication-v1",
        verified: true,
        assuranceLevel: "aal2",
      });
    }

    if (action === "mfa_unenroll") {
      const factorId = safeIdentifier(payload?.factorId);
      if (!factorId) return json({ error: "invalid_factor_id" }, 400);
      const [factorResult, assuranceResult] = await Promise.all([
        supabase.auth.mfa.listFactors(),
        supabase.auth.mfa.getAuthenticatorAssuranceLevel(),
      ]);
      if (factorResult.error) {
        return json({ error: "mfa_factor_list_unavailable" }, 503);
      }
      if (assuranceResult.error) {
        return json({ error: "assurance_level_unavailable" }, 503);
      }
      const targetFactor = (factorResult.data?.all ?? []).find(
        (factor) => factor.id === factorId,
      );
      if (!targetFactor) return json({ error: "mfa_factor_not_found" }, 404);
      if (targetFactor.factor_type !== "totp") {
        return json({ error: "unsupported_mfa_factor_type" }, 409);
      }
      const recentAal2 = evaluateRecentAal2(
        assuranceResult.data.currentLevel,
        assuranceResult.data.currentAuthenticationMethods,
      );
      if (!recentAal2.eligible) {
        return json({ error: recentAal2.limitation }, 403);
      }
      const { error } = await supabase.auth.mfa.unenroll({ factorId });
      if (error) return json({ error: "mfa_unenroll_failed" }, 400);
      // Supabase documents that the old JWT can remain aal2 until refresh.
      // Refresh immediately so the removed factor cannot leave a stale high-
      // assurance cookie. If refresh fails, revoke the local session rather
      // than returning a still-privileged browser to the application.
      const refreshResult = await supabase.auth.refreshSession();
      if (refreshResult.error || !refreshResult.data.session) {
        await supabase.auth.signOut({ scope: "local" }).catch(() => undefined);
        return json({ error: "mfa_unenrolled_reauthentication_required" }, 401);
      }
      return json({ unenrolled: true, factorId });
    }

    if (action === "identity_unlink") {
      const identityId = safeIdentifier(payload?.identityId);
      if (!identityId) return json({ error: "invalid_identity_id" }, 400);
      const { data: identitiesData, error: identitiesError } =
        await supabase.auth.getUserIdentities();
      if (identitiesError) return json({ error: "identity_list_unavailable" }, 503);
      const identities = identitiesData?.identities ?? [];
      const identity = identities.find((item) => item.identity_id === identityId);
      const unlinkEligibility = evaluateIdentityUnlink(
        identities.map((item) => ({
          identityId: item.identity_id,
          provider: item.provider,
        })),
        identityId,
      );
      if (!unlinkEligibility.eligible) {
        const status = unlinkEligibility.limitation === "identity_not_found" ? 404 : 409;
        return json({ error: unlinkEligibility.limitation }, status);
      }
      if (!identity) return json({ error: "identity_not_found" }, 404);
      const assuranceResult = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
      if (assuranceResult.error) {
        return json({ error: "assurance_level_unavailable" }, 503);
      }
      const recentAal2 = evaluateRecentAal2(
        assuranceResult.data.currentLevel,
        assuranceResult.data.currentAuthenticationMethods,
      );
      if (!recentAal2.eligible) {
        return json({ error: recentAal2.limitation }, 403);
      }
      const { error } = await supabase.auth.unlinkIdentity(identity);
      if (error) return json({ error: "identity_unlink_failed" }, 400);
      return json({ unlinked: true, identityId, provider: identity.provider });
    }

    return json({ error: "unsupported_security_action" }, 400);
  } catch {
    return json({ error: "supabase_auth_not_configured" }, 503);
  }
}
