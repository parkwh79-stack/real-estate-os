import { naverUserinfo } from "./handler.ts";

// This endpoint receives a Naver access token, not a Supabase JWT.
// Naver validates it server-side; no Supabase admin key or database write occurs.
Deno.serve((request: Request) => naverUserinfo(request));
