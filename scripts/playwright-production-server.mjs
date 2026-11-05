import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const webDirectory = path.join(repository, "apps", "web");
const port = Number(process.argv[2] ?? "3032");
if (!Number.isInteger(port) || port < 1024 || port > 65534) {
  throw new Error("Provide a loopback HTTPS port between 1024 and 65534.");
}
const publicOrigin = new URL(process.env.REALYTICS_WEB_ORIGIN ?? ("https://localhost:" + port));
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
if (publicOrigin.protocol !== "https:" || !loopbackHosts.has(publicOrigin.hostname)
  || Number(publicOrigin.port || "443") !== port || publicOrigin.username || publicOrigin.password) {
  throw new Error("The E2E origin must be HTTPS on the selected loopback port.");
}
const buildDirectory = path.resolve(webDirectory, process.env.REALYTICS_NEXT_DIST_DIR ?? ".next-e2e-production");
const relativeBuild = path.relative(webDirectory, buildDirectory);
if (relativeBuild.startsWith("..") || path.isAbsolute(relativeBuild)) {
  throw new Error("The E2E build must be inside apps/web.");
}
if (!existsSync(path.join(buildDirectory, "BUILD_ID"))) {
  throw new Error("Build the production E2E bundle before starting its HTTPS server.");
}

let certificateDirectory;
let nextProcess;
let proxy;
let stopping = false;
const upstreamAgent = new http.Agent({ keepAlive: true });

function removeCertificate() {
  if (!certificateDirectory) return;
  if (path.dirname(certificateDirectory) !== buildDirectory
    || !path.basename(certificateDirectory).startsWith(".playwright-tls-")) {
    throw new Error("Refusing to remove a certificate directory outside the E2E build.");
  }
  rmSync(certificateDirectory, { recursive: true, force: true });
  certificateDirectory = undefined;
}

async function shutdown(exitCode) {
  if (stopping) return;
  stopping = true;
  proxy?.close();
  proxy?.closeAllConnections();
  upstreamAgent.destroy();
  if (nextProcess?.pid && nextProcess.exitCode === null && nextProcess.signalCode === null) {
    const exited = new Promise((resolve) => nextProcess.once("exit", resolve));
    nextProcess.kill("SIGTERM");
    const forceStop = setTimeout(() => nextProcess.kill("SIGKILL"), 5000);
    await exited;
    clearTimeout(forceStop);
  }
  removeCertificate();
  process.exit(exitCode);
}

process.once("SIGINT", () => void shutdown(0));
process.once("SIGTERM", () => void shutdown(0));
process.once("exit", () => {
  if (nextProcess && nextProcess.exitCode === null) nextProcess.kill();
  upstreamAgent.destroy();
  removeCertificate();
});

try {
  const require = createRequire(import.meta.url);
  const nextExecutable = require.resolve("next/dist/bin/next");
  const opensslCandidates = [
    "openssl",
    ...(process.platform === "win32"
      ? [path.join(process.env.ProgramFiles ?? "C:\\Program Files", "Git", "usr", "bin", "openssl.exe")]
      : []),
  ];
  const openssl = opensslCandidates.find((candidate) => spawnSync(candidate, ["version"], {
    stdio: "ignore", windowsHide: true,
  }).status === 0);
  if (!openssl) throw new Error("OpenSSL is required to create the temporary loopback certificate.");
  certificateDirectory = mkdtempSync(path.join(buildDirectory, ".playwright-tls-"));
  const keyPath = path.join(certificateDirectory, "key.pem");
  const certificatePath = path.join(certificateDirectory, "cert.pem");
  const generated = spawnSync(openssl, [
    "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
    "-keyout", keyPath, "-out", certificatePath, "-days", "1",
    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1",
  ], {
    stdio: "ignore", windowsHide: true,
    env: { ...process.env, MSYS_NO_PATHCONV: "1", MSYS2_ARG_CONV_EXCL: "*" },
  });
  if (generated.status !== 0) throw new Error("Unable to create the temporary loopback certificate.");

  nextProcess = spawn(process.execPath, [nextExecutable, "start", "--hostname", "127.0.0.1", "--port", String(port + 1)], {
    cwd: webDirectory,
    stdio: "inherit",
    windowsHide: true,
    env: {
      ...process.env,
      REALYTICS_NEXT_DIST_DIR: relativeBuild,
      REALYTICS_WEB_ORIGIN: publicOrigin.origin,
      REALYTICS_API_ORIGIN: process.env.REALYTICS_API_ORIGIN ?? "https://realytics-api-obozektnwa-an.a.run.app",
    },
  });
  nextProcess.once("error", (error) => {
    console.error("The E2E Next server could not start.", error.code ?? "");
    void shutdown(1);
  });
  nextProcess.once("exit", (code) => { if (!stopping) void shutdown(code ?? 1); });

  proxy = https.createServer({
    key: readFileSync(keyPath), cert: readFileSync(certificatePath), minVersion: "TLSv1.2",
  }, (request, response) => {
    let host;
    try {
      host = new URL("https://" + (request.headers.host ?? ""));
      if (!loopbackHosts.has(host.hostname) || Number(host.port || "443") !== port
        || host.username || host.password || host.pathname !== "/") throw new Error("Invalid host");
    } catch {
      response.writeHead(400).end("Invalid E2E host");
      return;
    }
    const browserHeaders = Object.fromEntries(Object.entries(request.headers).filter(
      ([name]) => name !== "forwarded" && !name.startsWith("x-forwarded-"),
    ));
    const headers = {
      ...browserHeaders,
      host: host.host,
      "x-forwarded-host": host.host,
      "x-forwarded-proto": "https",
      "x-forwarded-port": String(port),
      "x-forwarded-for": request.socket.remoteAddress ?? "127.0.0.1",
    };
    const upstream = http.request({
      hostname: "127.0.0.1", port: port + 1, path: request.url, method: request.method, headers,
      agent: upstreamAgent,
    }, (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    });
    upstream.once("error", () => {
      if (!response.headersSent) response.writeHead(502);
      response.end("E2E application server unavailable");
    });
    request.once("aborted", () => upstream.destroy());
    response.once("close", () => { if (!response.writableEnded) upstream.destroy(); });
    request.pipe(upstream);
  });
  proxy.once("error", (error) => {
    console.error("The E2E HTTPS listener could not start.", error.code ?? "");
    void shutdown(1);
  });
  proxy.listen(port, publicOrigin.hostname === "[::1]" ? "::1" : "127.0.0.1", () => {
    console.log("REALYTICS production E2E HTTPS listener " + publicOrigin.origin);
  });
} catch (error) {
  console.error(error instanceof Error ? error.message : "The E2E HTTPS server failed.");
  void shutdown(1);
}
