import assert from "node:assert/strict";
import test from "node:test";
import { naverUserinfo } from "../../supabase/functions/naver-userinfo/handler.ts";

const request = () => new Request("https://adapter.test/?sub=attacker", {
  headers: { authorization: "Bearer naver-fixture-token" },
});

test("Naver adapter trusts only verified profile ID and excludes unverified email/roles", async () => {
  const result = await naverUserinfo(request(), async (url, options) => {
    assert.equal(url, "https://openapi.naver.com/v1/nid/me");
    assert.equal(options.headers.Authorization, "Bearer naver-fixture-token");
    assert.equal(options.redirect, "error");
    return Response.json({ resultcode: "00", response: {
      id: "naver-stable-id", nickname: "샘플 사용자", email: "existing@example.com",
      profile_image: "https://profile.test/image.png", role: "admin",
    } });
  });
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), {
    sub: "naver-stable-id", name: "샘플 사용자", picture: "https://profile.test/image.png",
  });
  assert.match(result.headers.get("cache-control"), /no-store/);
});

test("Naver adapter refuses missing tokens and wrong methods without contacting provider", async () => {
  const unexpected = async () => { throw new Error("must not fetch"); };
  assert.equal((await naverUserinfo(new Request("https://adapter.test"), unexpected)).status, 401);
  assert.equal((await naverUserinfo(new Request("https://adapter.test", { method: "POST" }), unexpected)).status, 405);
});

test("Naver adapter never accepts failed or missing provider identities", async () => {
  for (const body of [{ resultcode: "024", response: { id: "bad" } }, { resultcode: "00", response: {} }, null]) {
    const result = await naverUserinfo(request(), async () => Response.json(body));
    assert.equal(result.status, 401);
    assert.equal((await result.json()).sub, undefined);
  }
  assert.equal((await naverUserinfo(request(), async () => new Response("upstream secret", { status: 401 }))).status, 401);
});

test("Naver adapter reports upstream outage without disclosing token or provider response", async () => {
  for (const fetcher of [async () => new Response("secret", { status: 500 }), async () => { throw new Error("secret"); }]) {
    const result = await naverUserinfo(request(), fetcher);
    assert.equal(result.status, 503);
    assert.deepEqual(await result.json(), { error: "provider_unavailable" });
  }
});
