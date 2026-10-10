#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Gilbert Kamau
"use strict";

const { spawn } = require("node:child_process");
const path = require("node:path");

const url = process.env.FPL_MCP_URL || "https://fpl-mcp-9tiv.onrender.com/mcp";
const args = [url, "--transport", "http-only"];
if (process.env.MCP_AUTH_TOKEN) {
  args.push("--header", `Authorization: Bearer ${process.env.MCP_AUTH_TOKEN}`);
}

const pkg = require.resolve("mcp-remote/package.json");
const proxy = path.join(path.dirname(pkg), require(pkg).bin["mcp-remote"]);

const child = spawn(process.execPath, [proxy, ...args, ...process.argv.slice(2)], { stdio: "inherit" });
child.on("exit", (code, signal) => (signal ? process.kill(process.pid, signal) : process.exit(code ?? 0)));
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
