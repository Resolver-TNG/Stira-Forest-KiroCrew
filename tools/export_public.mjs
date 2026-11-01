#!/usr/bin/env node
// Build the public repository from this one, as a filtered snapshot.
//
//   node tools/export_public.mjs <public-checkout> [--commit]
//
// - Takes the files git tracks at HEAD, minus the prefixes in .publicignore.
// - <public-checkout> is a clone of the public repo (or an empty folder: it is `git init`ed).
//   Its tracked files are replaced, so files removed here disappear there too.
// - Refuses to run with uncommitted changes, and fails when a copied file contains a
//   personal path or a credential-shaped string.
// - With --commit, commits as "Stira Forest v<version>" (app.json). Never pushes.
//
// The public history is one commit per release; this repository keeps the full history.

import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const target = args.find((a) => !a.startsWith('--'));
const commit = args.includes('--commit');
if (!target) {
  console.error('usage: node tools/export_public.mjs <public-checkout> [--commit]');
  process.exit(2);
}
const out = resolve(target);
if (out === repo || out.startsWith(repo + '\\') || out.startsWith(repo + '/')) {
  console.error('the public checkout must be outside this repository');
  process.exit(2);
}

const git = (cwd, ...a) => execFileSync('git', a, { cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });

if (git(repo, 'status', '--porcelain', '--untracked-files=no').trim()) {
  console.error('uncommitted changes: commit first, so the export matches a commit');
  process.exit(1);
}

const ignore = readFileSync(join(repo, '.publicignore'), 'utf8')
  .split(/\r?\n/)
  .map((l) => l.trim())
  .filter((l) => l && !l.startsWith('#'));
const files = git(repo, 'ls-files', '-z')
  .split('\0')
  .filter(Boolean)
  .filter((f) => !ignore.some((p) => f === p || f.startsWith(p)));

// Personal paths and credential shapes. Built bundles and binary assets are skipped.
const BAD = new RegExp(
  [
    String.raw`[A-Za-z]:\\Users\\[A-Za-z0-9._-]+`,
    String.raw`/Users/[a-z][A-Za-z0-9._-]+`,
    String.raw`/home/[a-z][A-Za-z0-9._-]+`,
    String.raw`gh[pousr]_[A-Za-z0-9]{20,}`,
    String.raw`github_pat_[A-Za-z0-9_]{20,}`,
    String.raw`sk-[A-Za-z0-9_-]{20,}`,
    String.raw`AKIA[0-9A-Z]{16}`,
    String.raw`-----BEGIN [A-Z ]*PRIVATE KEY-----`,
  ].join('|'),
);
// ui/dist/index.mjs is a minified React bundle; assets and fonts are binary.
const SKIP_SCAN = /^ui\/dist\/|\.(png|jpe?g|gif|webp|woff2?|ttf|glb|bin)$/i;

if (!existsSync(out)) mkdirSync(out, { recursive: true });
if (!existsSync(join(out, '.git'))) git(out, 'init', '-q', '-b', 'main');
for (const f of git(out, 'ls-files', '-z').split('\0').filter(Boolean)) rmSync(join(out, f), { force: true });

const hits = [];
for (const f of files) {
  const src = join(repo, f);
  if (!existsSync(src) || !statSync(src).isFile()) continue;
  if (!SKIP_SCAN.test(f)) {
    const text = readFileSync(src, 'utf8');
    const m = text.match(BAD);
    if (m) hits.push(`${f}: ${m[0]}`);
  }
  const dst = join(out, f);
  mkdirSync(dirname(dst), { recursive: true });
  copyFileSync(src, dst);
}
if (hits.length) {
  console.error('personal path or credential-shaped text found:\n  ' + hits.join('\n  '));
  process.exit(1);
}

git(out, 'add', '-A');
const version = JSON.parse(readFileSync(join(repo, 'app.json'), 'utf8')).version;
const changed = git(out, 'status', '--porcelain').trim();
console.log(`exported ${files.length} files to ${out} (v${version}); ${changed ? changed.split('\n').length : 0} changed`);
if (commit && changed) {
  git(out, 'commit', '-q', '-m', `Stira Forest v${version}`);
  console.log(git(out, 'log', '--oneline', '-1').trim());
}
