import { createReadStream, existsSync } from 'node:fs'
import { mkdir, mkdtemp, rm, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { dirname, extname, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const uiDir = dirname(scriptDir)
const previewDist = resolve(uiDir, 'preview-dist')
const indexPath = join(previewDist, 'index.html')

const mimeTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
])

function envValue(name) {
  const key = Object.keys(process.env).find((candidate) => candidate.toLowerCase() === name.toLowerCase())
  return key ? process.env[key] : undefined
}

function chromePath() {
  const programFiles = envValue('ProgramFiles') || 'C:\\Program Files'
  const programFilesX86 = envValue('ProgramFiles(x86)') || 'C:\\Program Files (x86)'
  const candidates = [
    join(programFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    join(programFilesX86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ]
  const found = candidates.find((candidate) => existsSync(candidate))
  if (!found) {
    throw new Error(`Google Chrome was not found. Checked:\n${candidates.join('\n')}`)
  }
  return found
}

function safeAssetPath(requestUrl) {
  const pathname = decodeURIComponent(new URL(requestUrl || '/', 'http://127.0.0.1').pathname)
  const relative = pathname.replace(/^\/+/, '') || 'index.html'
  const target = resolve(previewDist, relative)
  if (target !== previewDist && !target.startsWith(`${previewDist}${sep}`)) return null
  return target
}

async function startServer() {
  const server = createServer(async (request, response) => {
    try {
      let target = safeAssetPath(request.url)
      if (!target) {
        response.writeHead(403).end('Forbidden')
        return
      }
      const targetStat = await stat(target)
      if (targetStat.isDirectory()) target = join(target, 'index.html')
      response.writeHead(200, {
        'Cache-Control': 'no-store',
        'Content-Type': mimeTypes.get(extname(target).toLowerCase()) || 'application/octet-stream',
      })
      createReadStream(target).pipe(response)
    } catch {
      response.writeHead(404).end('Not found')
    }
  })

  await new Promise((resolveListening, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolveListening)
  })
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Could not determine preview server port')
  return { server, url: `http://127.0.0.1:${address.port}/` }
}

async function capture(chrome, url, outputPath, profileDir) {
  const args = [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars',
    '--no-first-run',
    '--no-default-browser-check',
    '--run-all-compositor-stages-before-draw',
    '--virtual-time-budget=3000',
    '--window-size=2300,1100',
    `--user-data-dir=${profileDir}`,
    `--screenshot=${outputPath}`,
    url,
  ]

  const result = await new Promise((resolveExit, reject) => {
    const child = spawn(chrome, args, { stdio: ['ignore', 'ignore', 'pipe'] })
    let stderr = ''
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.once('error', reject)
    child.once('exit', (code, signal) => resolveExit({ code, signal, stderr }))
  })

  if (result.code !== 0) {
    throw new Error(`Chrome screenshot failed (code=${result.code}, signal=${result.signal ?? 'none'}):\n${result.stderr}`)
  }
  if (!existsSync(outputPath)) throw new Error(`Chrome exited successfully but did not create ${outputPath}`)
}

async function main() {
  if (!existsSync(indexPath)) {
    throw new Error(`Preview build not found at ${indexPath}. Run: npx vite build -c vite.preview.config.ts`)
  }

  const scratch = envValue('KIROCREW_SCRATCH')?.trim()
  const outputDir = scratch ? resolve(scratch) : previewDist
  await mkdir(outputDir, { recursive: true })
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
  const outputPath = join(outputDir, `stira-layout-preview-${timestamp}.png`)
  const profileDir = await mkdtemp(join(outputDir, '.stira-chrome-'))
  const { server, url } = await startServer()

  try {
    await capture(chromePath(), url, outputPath, profileDir)
    console.log(outputPath)
  } finally {
    await new Promise((resolveClose) => server.close(resolveClose))
    await rm(profileDir, { recursive: true, force: true })
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
})
