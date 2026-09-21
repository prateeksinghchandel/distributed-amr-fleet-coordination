#!/usr/bin/env node
/**
 * Cross-platform runner for the Python Fleet Manager.
 * Works seamlessly on Windows, Linux, and macOS without POSIX shell syntax.
 */

import { spawn } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const BACKEND = path.join(ROOT, 'backend');

function findPython() {
  if (process.env.PYTHON_BIN && fs.existsSync(process.env.PYTHON_BIN)) {
    return path.resolve(process.env.PYTHON_BIN);
  }

  const candidates = [
    path.join(ROOT, '.venv', 'Scripts', 'python.exe'),
    path.join(ROOT, '.venv', 'bin', 'python'),
    path.join(ROOT, 'venv', 'Scripts', 'python.exe'),
    path.join(ROOT, 'venv', 'bin', 'python'),
  ];

  for (const cand of candidates) {
    if (fs.existsSync(cand)) {
      return cand;
    }
  }

  return process.platform === 'win32' ? 'python' : 'python3';
}

const pythonBin = findPython();
const existingPythonPath = process.env.PYTHONPATH || '';
const newPythonPath = existingPythonPath
  ? `${BACKEND}${path.delimiter}${existingPythonPath}`
  : BACKEND;

const env = {
  ...process.env,
  PYTHONPATH: newPythonPath,
  PYTHONUNBUFFERED: '1',
};

const child = spawn(pythonBin, ['-m', 'fleet_manager'], {
  cwd: BACKEND,
  env,
  stdio: 'inherit',
});

child.on('exit', (code, signal) => {
  process.exit(code ?? (signal ? 1 : 0));
});

process.on('SIGINT', () => {
  child.kill('SIGINT');
});

process.on('SIGTERM', () => {
  child.kill('SIGTERM');
});
