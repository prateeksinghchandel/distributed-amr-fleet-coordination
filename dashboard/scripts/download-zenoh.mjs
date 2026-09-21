#!/usr/bin/env node
/**
 * download-zenoh.mjs — Cross-platform Zenoh standalone binary installer.
 *
 * Automatically downloads and unpacks matching zenohd and bridge binaries/plugins
 * for Windows, Linux, and macOS into dashboard/tools/zenoh/.
 */

import fs from 'node:fs';
import path from 'node:path';
import https from 'node:https';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TOOLS_DIR = path.resolve(__dirname, '..', 'tools', 'zenoh');
const ZENOH_VER = '1.10.1';

fs.mkdirSync(TOOLS_DIR, { recursive: true });

function detectTarget() {
  const platform = process.platform;
  const arch = process.arch;

  if (platform === 'win32') {
    return {
      os: 'windows',
      routerZip: `zenoh-${ZENOH_VER}-x86_64-pc-windows-msvc-standalone.zip`,
      bridgeZip: `zenoh-ts-${ZENOH_VER}-x86_64-pc-windows-msvc-standalone.zip`,
    };
  }
  if (platform === 'linux') {
    const archTag = arch === 'arm64' ? 'aarch64' : 'x86_64';
    return {
      os: 'linux',
      routerZip: `zenoh-${ZENOH_VER}-${archTag}-unknown-linux-gnu-standalone.zip`,
      bridgeZip: `zenoh-ts-${ZENOH_VER}-${archTag}-unknown-linux-gnu-standalone.zip`,
    };
  }
  if (platform === 'darwin') {
    const archTag = arch === 'arm64' ? 'aarch64' : 'x86_64';
    return {
      os: 'darwin',
      routerZip: `zenoh-${ZENOH_VER}-${archTag}-apple-darwin-standalone.zip`,
      bridgeZip: `zenoh-ts-${ZENOH_VER}-${archTag}-apple-darwin-standalone.zip`,
    };
  }
  throw new Error(`Unsupported OS: ${platform} (${arch})`);
}

function downloadFile(url, destPath) {
  return new Promise((resolve, reject) => {
    console.log(`Downloading: ${url}`);
    const req = https.get(url, { headers: { 'User-Agent': 'Node.js/ZenohDownloader' } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // Handle redirect
        downloadFile(res.headers.location, destPath).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} from ${url}`));
        return;
      }
      const out = fs.createWriteStream(destPath);
      res.pipe(out);
      out.on('finish', () => out.close(resolve));
      out.on('error', reject);
    });
    req.on('error', reject);
  });
}

function extractZip(zipPath, targetDir) {
  console.log(`Extracting: ${path.basename(zipPath)} -> ${targetDir}`);
  // Use Python's built-in zipfile module for reliable cross-platform unzipping
  const pyCmd = process.platform === 'win32' ? 'python' : 'python3';
  const res = spawnSync(pyCmd, ['-m', 'zipfile', '-e', zipPath, targetDir]);
  if (res.status !== 0) {
    throw new Error(`Failed to extract ${zipPath}: ${res.stderr?.toString()}`);
  }
}

async function main() {
  const target = detectTarget();
  console.log(`Target platform: ${target.os} (${process.arch})`);

  const routerUrl = `https://github.com/eclipse-zenoh/zenoh/releases/download/${ZENOH_VER}/${target.routerZip}`;
  const bridgeUrl = `https://github.com/eclipse-zenoh/zenoh-ts/releases/download/${ZENOH_VER}/${target.bridgeZip}`;

  const tmpRouterZip = path.join(TOOLS_DIR, target.routerZip);
  const tmpBridgeZip = path.join(TOOLS_DIR, target.bridgeZip);

  try {
    await downloadFile(routerUrl, tmpRouterZip);
    extractZip(tmpRouterZip, TOOLS_DIR);

    await downloadFile(bridgeUrl, tmpBridgeZip);
    extractZip(tmpBridgeZip, TOOLS_DIR);

    // Make POSIX binaries executable
    if (process.platform !== 'win32') {
      const binaries = ['zenohd', 'zenoh-bridge-remote-api'];
      for (const b of binaries) {
        const p = path.join(TOOLS_DIR, b);
        if (fs.existsSync(p)) {
          fs.chmodSync(p, 0o755);
        }
      }
    }

    console.log(`Zenoh binaries installed successfully to: ${TOOLS_DIR}`);
    console.log('Installed files:', fs.readdirSync(TOOLS_DIR));
  } finally {
    // Clean up temporary zip downloads
    if (fs.existsSync(tmpRouterZip)) fs.unlinkSync(tmpRouterZip);
    if (fs.existsSync(tmpBridgeZip)) fs.unlinkSync(tmpBridgeZip);
  }
}

main().catch((err) => {
  console.error(`Zenoh download failed: ${err.message}`);
  process.exit(1);
});
