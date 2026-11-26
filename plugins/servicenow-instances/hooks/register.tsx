import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Check, Instance, Nav, Server, View } from '../types'

const PANE = 'servicenow-instances'
const TITLE = 'ServiceNow instances'
// An archive, so installing needs Python alone and no git.
const PACKAGE =
  'https://github.com/R2gers/servicenow-mcp/archive/refs/heads/master.zip'
const VERSION_CODE = 'import servicenow_mcp; print(servicenow_mcp.__version__)'
const SETUP_CHECK_CODE =
  'import json; from servicenow_mcp import setup; print(json.dumps(setup.check()))'
const PYTHONS = ['python', 'python3', 'py']
const NO_INSTANCES: Instance[] = []
const NO_CHECKS: Record<string, Check> = {}
const LIST_VIEW: View = { mode: 'list', auth: 'basic', gen: 0, message: '' }
const UNKNOWN_SERVER: Server = { status: 'unknown', python: '', text: '' }
const LIST_SCREEN: Nav = { screen: 'list', name: '' }
// A longer list is searched, not scrolled.
const MAX_ROWS = 40
// Up to this many show as whole cards; more show one line each.
const OPEN_CARDS = 6

// The name of the instance whose Allow writes press awaits its confirmation.
const confirm = atom(
  { plugin: 'servicenow-instances', key: 'confirm' } as const,
  '',
)
const nav = atom(
  { plugin: 'servicenow-instances', key: 'nav' } as const,
  LIST_SCREEN,
)
const query = atom({ plugin: 'servicenow-instances', key: 'query' } as const, '')
const server = atom(
  { plugin: 'servicenow-instances', key: 'server' } as const,
  UNKNOWN_SERVER,
)
const base = atom({ plugin: 'servicenow-instances', key: 'base' } as const, '')
const instances = atom(
  { plugin: 'servicenow-instances', key: 'instances' } as const,
  NO_INSTANCES,
)
const checks = atom(
  { plugin: 'servicenow-instances', key: 'checks' } as const,
  NO_CHECKS,
)
const view = atom(
  { plugin: 'servicenow-instances', key: 'view' } as const,
  LIST_VIEW,
)

// A press is given ten seconds and a command can take longer, and work a press
// leaves running is dropped with it: such work is handed to a timer, which the
// engine runs once the press has returned.
const later = ($: EngineInterface, work: () => Promise<unknown>) => {
  $.clock.after(0, () => {
    void work().catch(() => undefined)
  })
}

const sepOf = (path: string) => (path.includes('\\') ? '\\' : '/')
const join = (dir: string, name: string) =>
  `${dir.replace(/[\\/]+$/, '')}${sepOf(dir)}${name}`
const parentOf = (path: string) => {
  const trimmed = path.replace(/[\\/]+$/, '')
  const cut = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'))

  return cut <= 0 ? '' : trimmed.slice(0, cut)
}

// The server reads .env with python-dotenv, interpolation off: a value outside
// the plain set is double-quoted with `\` and `"` escaped.
const quote = (value: string) =>
  /^[A-Za-z0-9_@%+=:,./-]+$/.test(value)
    ? value
    : `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`

const parseEnv = (text: string) => {
  const found: Record<string, string> = {}

  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*?)\s*$/.exec(line)
    if (match?.[1] === undefined) continue
    found[match[1]] = (match[2] ?? '').replace(/^(['"])(.*)\1$/, '$2')
  }

  return found
}

const originOf = (raw: string) => {
  const text = raw.trim()
  if (/^http:\/\//i.test(text)) return undefined
  const withScheme = /^https:\/\//i.test(text) ? text : `https://${text}`
  if (!URL.canParse(withScheme)) return undefined
  const url = new URL(withScheme)

  return url.hostname.includes('.') ? url.origin : undefined
}

const scan = async ($: EngineInterface, dir: string) => {
  const entries = await $.fs.list(dir).catch(() => [])
  const names = entries
    .filter(entry => entry.kind === 'dir' && !entry.name.startsWith('.'))
    .map(entry => entry.name)
    .sort()
    .slice(0, 60)
  const found: Instance[] = []

  for (const name of names) {
    const folder = join(dir, name)
    const envPath = join(folder, '.env')
    if (!(await $.fs.exists(envPath).catch(() => false))) continue
    const text = await $.fs.read(envPath).catch(() => '')
    const env = parseEnv(text)
    const url = env['SERVICENOW_INSTANCE_URL']
    if (url === undefined || url === '') continue
    found.push({
      name,
      folder,
      url,
      auth: env['SERVICENOW_AUTH_TYPE'] ?? 'basic',
      user: env['SERVICENOW_USERNAME'] ?? '',
    })
  }

  return found
}

const findBase = async ($: EngineInterface) => {
  const current = await read($, base)
  if (current !== '') return current
  const stored = await $.store.get('base')
  if (typeof stored === 'string' && stored !== '') return stored
  const root = await $.session.root()
  const parent = parentOf(root)
  if (parent !== '' && (await scan($, parent)).length > 0) return parent
  const home =
    (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? root

  return join(home, 'ServiceNow')
}

const setupProblems = (stdout: string): string[] | undefined => {
  const line = stdout
    .split(/\r?\n/)
    .reverse()
    .find(one => one.trim().startsWith('{'))
  if (line === undefined) return undefined
  try {
    const parsed: unknown = JSON.parse(line)
    if (typeof parsed !== 'object' || parsed === null) return undefined
    if (!('problems' in parsed) || !Array.isArray(parsed.problems)) {
      return undefined
    }

    return parsed.problems.map(String)
  } catch {
    return undefined
  }
}

const findServer = async ($: EngineInterface): Promise<Server> => {
  let python = ''

  for (const exe of PYTHONS) {
    const ran = await $.process
      .run([exe, '-c', VERSION_CODE], { timeoutMs: 20000 })
      .catch(() => undefined)
    if (ran === undefined) continue
    const version = ran.stdout.trim()
    if (ran.exitCode === 0 && /^\d+(\.\d+)+$/.test(version)) {
      // Reads only. An older server has no such check and answers nothing.
      const checked = await $.process
        .run([exe, '-c', SETUP_CHECK_CODE], { timeoutMs: 20000 })
        .catch(() => undefined)
      const problems = setupProblems(checked?.stdout ?? '')
      if (problems !== undefined && problems.length > 0) {
        return {
          status: 'needs-setup',
          python: exe,
          text: `Setup is incomplete. ${problems.join(' ')}`,
        }
      }

      return {
        status: 'installed',
        python: exe,
        text: `Server: servicenow-mcp ${version} is installed.`,
      }
    }
    if (python === '' && ran.stderr.includes('servicenow_mcp')) python = exe
  }

  return python === ''
    ? {
        status: 'no-python',
        python: '',
        text: 'Server: Python 3.10 or later was not found. Install Python, then press Refresh.',
      }
    : {
        status: 'missing',
        python,
        text: 'Server: servicenow-mcp is not installed yet.',
      }
}

const lastLine = (text: string | undefined) =>
  (text ?? '').trim().split(/\r?\n/).pop()?.slice(0, 300) ?? ''

// Run only by the person's press on Install server, and only while the server
// is missing: it never replaces an install that is already there.
const install = async ($: EngineInterface) => {
  const before = await read($, server)
  if (before.status !== 'missing') return
  const exe = before.python
  await update($, server, () => ({
    status: 'installing' as const,
    python: exe,
    text: 'Server: installing servicenow-mcp. This can take a minute...',
  }))
  const pip = await $.process
    .run([exe, '-m', 'pip', 'install', '--upgrade', PACKAGE], {
      timeoutMs: 600000,
    })
    .catch(() => undefined)
  if (pip === undefined || pip.exitCode !== 0) {
    const why = lastLine(pip?.stderr)
    await update($, server, () => ({
      status: 'missing' as const,
      python: exe,
      text: `Server: pip could not install servicenow-mcp. ${why}`,
    }))

    return
  }
  // Registers the MCP server, the /servicenow skill and the write confirmations.
  const setup = await $.process
    .run([exe, '-m', 'servicenow_mcp.setup', '--quick'], { timeoutMs: 120000 })
    .catch(() => undefined)
  const found = await findServer($)
  const note =
    setup?.exitCode === 0
      ? 'Restart Claude Code to load it.'
      : 'Its setup did not finish: run servicenow-mcp-setup --quick in a terminal.'
  await update($, server, () => ({ ...found, text: `${found.text} ${note}` }))
  await say($, `Server installed. ${note}`)
}

// Run only by the person's press on Finish setup: the server's own setup
// installs the write confirmations, the skill and the MCP registration, and
// keeps the settings file as it was beside it.
const finishSetup = async ($: EngineInterface) => {
  const before = await read($, server)
  if (before.status !== 'needs-setup') return
  const exe = before.python
  await update($, server, () => ({
    status: 'installing' as const,
    python: exe,
    text: 'Finishing setup...',
  }))
  const ran = await $.process
    .run([exe, '-m', 'servicenow_mcp.setup', '--quick'], { timeoutMs: 120000 })
    .catch(() => undefined)
  const found = await findServer($)
  await update($, server, () => found)
  await say(
    $,
    ran?.exitCode === 0 && found.status === 'installed'
      ? 'Setup finished. Restart any open ServiceNow session for it to take effect. Your previous Claude settings were kept as settings.json.before-servicenow-mcp.'
      : 'Setup did not finish: run servicenow-mcp-setup --quick in a terminal.',
  )
}

const refresh = async ($: EngineInterface) => {
  const dir = await findBase($)
  await update($, base, () => dir)
  const found = await scan($, dir)
  await update($, instances, () => found)
  const current = await findServer($)
  await update($, server, () => current)
}

const useBase = async ($: EngineInterface, dir: string) => {
  await $.store.set('base', dir)
  await update($, base, () => dir)
  await refresh($)
}

const say = ($: EngineInterface, message: string) =>
  update($, view, now => ({ ...now, message }))

type Report = {
  ok?: boolean
  user?: string
  host?: string
  access_mode?: string
  error?: string
}

const reportOf = (stdout: string): Report | undefined => {
  const line = stdout
    .split(/\r?\n/)
    .reverse()
    .find(one => one.trim().startsWith('{'))
  if (line === undefined) return undefined
  try {
    return JSON.parse(line) as Report
  } catch {
    return undefined
  }
}

const describe = (report: Report): Check => {
  if (report.ok !== true) {
    const error = (report.error ?? 'The check failed.').slice(0, 900)

    return { ok: false, isRunning: false, text: `Not connected: ${error}` }
  }
  const who = `Connected as ${report.user ?? 'the API user'}.`
  if (report.access_mode === 'write') {
    const text = `${who} Claude may write to this instance.`

    return { ok: true, isRunning: false, text, access: 'write' }
  }
  const text = `${who} Read-only for Claude.`
  const access = 'read-only' as const

  return report.host === undefined || report.host === ''
    ? { ok: true, isRunning: false, text, access }
    : { ok: true, isRunning: false, text, access, allowHost: report.host }
}

const markOf = (result: Check | undefined) =>
  result === undefined ? '○' : result.isRunning ? '…' : '●'
const colorOf = (result: Check | undefined) =>
  result === undefined || result.isRunning
    ? undefined
    : result.ok
      ? 'green'
      : 'red'
const tagOf = (result: Check | undefined) => {
  if (result === undefined) return 'not tested'
  if (result.isRunning) return 'checking'
  if (!result.ok) return 'not connected'
  if (result.access === 'write') return 'writable'

  // A result an earlier version stored says nothing of access: claim nothing.
  return result.access === 'read-only' || result.allowHost !== undefined
    ? 'read-only'
    : 'connected'
}
const tagColorOf = (tag: string) =>
  tag === 'writable' || tag === 'connected'
    ? 'green'
    : tag === 'read-only'
      ? 'yellow'
      : tag === 'not connected'
        ? 'red'
        : undefined
const hostOf = (url: string) =>
  url.replace(/^https?:\/\//i, '').replace(/\/+$/, '')

// Leaving a screen drops a half-given confirmation and the last message.
const goTo = async ($: EngineInterface, screen: Nav['screen'], name: string) => {
  await update($, confirm, () => '')
  await update($, view, now => ({ ...now, message: '' }))
  await update($, nav, () => ({ screen, name }))
}

const doctor = async ($: EngineInterface, one: Instance): Promise<Check> => {
  let isOutdated = false

  for (const exe of PYTHONS) {
    const argv = [
      exe,
      '-m',
      'servicenow_mcp.doctor',
      '--env-file',
      join(one.folder, '.env'),
    ]
    const ran = await $.process
      .run(argv, { cwd: one.folder, timeoutMs: 60000 })
      .catch(() => undefined)
    if (ran === undefined) continue
    const report = reportOf(ran.stdout)
    if (report !== undefined) return describe(report)
    if (
      ran.stderr.includes('servicenow_mcp.doctor') &&
      !ran.stderr.includes("No module named 'servicenow_mcp'")
    ) {
      isOutdated = true
    }
  }

  return {
    ok: false,
    isRunning: false,
    text: isOutdated
      ? 'The installed servicenow-mcp is too old for this check. Run: servicenow-mcp-update'
      : 'Could not run the check: the servicenow-mcp server is not installed. Press Refresh, then Install server.',
  }
}

const runCheck = async ($: EngineInterface, one: Instance) => {
  const running: Check = { ok: false, isRunning: true, text: 'Checking...' }
  await update($, checks, all => ({ ...all, [one.name]: running }))
  const result = await doctor($, one)
  await update($, checks, all => ({ ...all, [one.name]: result }))
}

// Pressing a card's name opens it, and pressing the open one closes it.
const toggle = async ($: EngineInterface, name: string) => {
  const { name: open } = await read($, nav)
  await goTo($, 'list', open === name ? '' : name)
}

// One after another: a row's mark fills in as its answer comes.
const testAll = async ($: EngineInterface, list: readonly Instance[]) => {
  for (const one of list) {
    await runCheck($, one)
  }
}

// Run only by the person's second press, on the confirmation: it has the
// server's own setup command record the approval, so an organisation's policy
// still decides where one is installed. The model has no way to reach it.
const allowWrites = async ($: EngineInterface, one: Instance, host: string) => {
  await update($, confirm, () => '')
  const { python } = await read($, server)
  const exe = python === '' ? 'python' : python
  const ran = await $.process
    .run([exe, '-m', 'servicenow_mcp.setup', '--allow-write', host], {
      timeoutMs: 60000,
    })
    .catch(() => undefined)
  if (ran?.exitCode !== 0) {
    const why = lastLine(ran?.stdout) || lastLine(ran?.stderr)
    const failed: Check = {
      ok: false,
      isRunning: false,
      text: `Writes were not allowed. ${why}`,
    }
    await update($, checks, all => ({ ...all, [one.name]: failed }))

    return
  }
  await runCheck($, one)
  await say(
    $,
    `Writes allowed on ${host}. Restart any session already open on that instance.`,
  )
}

const textOf = (content: unknown) =>
  Array.isArray(content)
    ? content
        .map((block: unknown) =>
          typeof block === 'object' && block !== null && 'text' in block
            ? String(block.text)
            : '',
        )
        .join(' ')
        .trim()
        .slice(0, 300)
    : ''

// The MCP server reads the .env of the folder its session starts in, so an
// instance is worked on from a session of its own. Only the Claude desktop app
// has the server that starts one; anywhere else the pane says where to go.
const openSession = async ($: EngineInterface, one: Instance) => {
  let refusal = ''
  const started = await $.mcp
    .call('ccd_session', 'start_session', {
      initiation: 'user_asked',
      title: `ServiceNow: ${one.name}`.slice(0, 58),
      context: 'fresh',
      cwd: one.folder,
      use_worktree: false,
      prompt:
        'Call `sn_get_context` and tell me the instance, whether it is writable, and my current application and update set. Change nothing.',
      background: `This session was opened from the ServiceNow instances pane to work on the instance at ${one.url}. Its credentials are in the .env of this folder, which the servicenow MCP server reads when the session starts. After reporting the context, wait for the developer's instructions.`,
    })
    .catch((error: unknown) => {
      refusal = error instanceof Error ? error.message : String(error)

      return undefined
    })
  if (started !== undefined && !started.isError) {
    await say(
      $,
      `Opened a session for ${one.name}: look for "ServiceNow: ${one.name}" in the sidebar.`,
    )

    return
  }
  // The desktop app starts a session only on words the person sent themself: a
  // plugin's call is refused outside a turn of theirs, and so is a prompt a
  // plugin submits. So the request is written into the prompt box, theirs to
  // send; the refusal is kept for whoever looks into it.
  const why = (textOf(started?.content) || refusal).slice(0, 300)
  await $.store.set('lastOpenRefusal', why === '' ? 'no reason given' : why)
  const request = `Open a ServiceNow session for my instance "${one.name}": start a separate new session in the folder \`${one.folder}\`, titled "ServiceNow: ${one.name}". Its first task is to call \`sn_get_context\` and tell me the instance, whether it is writable, and my current application and update set, changing nothing, then wait for my instructions there.`
  const draft = await $.prompt.read()
  const filled = await $.prompt
    .fill(
      draft.text.trim() === ''
        ? { text: request }
        : { text: `\n\n${request}`, mode: 'append' },
    )
    .catch(() => undefined)
  await say(
    $,
    filled?.isFilled === true
      ? `The request to open ${one.name} is in the chat box: press Enter there to send it, and Claude opens the session.`
      : `Could not open a session from here. Start a new session yourself in the folder ${one.folder}.`,
  )
}

// What the person typed into the form. Kept here only: never in state, the
// store or the conversation, and emptied once the .env is written.
let draft: Record<string, string> = {}

const openForm = ($: EngineInterface, auth: View['auth']) => {
  draft = {}

  return update($, view, now => ({
    mode: 'form' as const,
    auth,
    gen: now.gen + 1,
    message: '',
  }))
}

const closeForm = ($: EngineInterface, message: string) => {
  draft = {}

  return update($, view, now => ({
    mode: 'list' as const,
    auth: now.auth,
    gen: now.gen + 1,
    message,
  }))
}

const save = async ($: EngineInterface) => {
  const { auth } = await read($, view)
  const name = (draft['name'] ?? '').trim()
  const url = originOf(draft['url'] ?? '')
  const user = (draft['user'] ?? '').trim()
  const password = draft['password'] ?? ''
  const clientId = (draft['clientId'] ?? '').trim()
  const clientSecret = draft['clientSecret'] ?? ''
  const tokenUrl = (draft['tokenUrl'] ?? '').trim()

  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name)) {
    return say($, 'Name: use letters, digits, dots, dashes or underscores (it becomes the folder name).')
  }
  if (url === undefined) {
    return say($, 'Instance URL: enter an https address such as https://dev12345.service-now.com')
  }
  if (user === '' || password === '') {
    return say($, 'Username and password are both required.')
  }
  if (auth === 'oauth' && (clientId === '' || clientSecret === '')) {
    return say($, 'OAuth needs a client ID and a client secret.')
  }
  const values = [user, password, clientId, clientSecret, tokenUrl]
  if (values.some(value => /[\r\n\0]/.test(value))) {
    return say($, 'A value holds a line break, which a .env file cannot store.')
  }

  const dir = await read($, base)
  const folder = join(dir, name)
  const envPath = join(folder, '.env')
  if (await $.fs.exists(envPath)) {
    return say($, `${folder} already has a .env. Choose another name.`)
  }

  const lines = [
    `SERVICENOW_INSTANCE_URL=${url}`,
    `SERVICENOW_AUTH_TYPE=${auth}`,
    `SERVICENOW_USERNAME=${quote(user)}`,
    `SERVICENOW_PASSWORD=${quote(password)}`,
  ]
  if (auth === 'oauth') {
    lines.push(
      `SERVICENOW_CLIENT_ID=${quote(clientId)}`,
      `SERVICENOW_CLIENT_SECRET=${quote(clientSecret)}`,
      `SERVICENOW_TOKEN_URL=${quote(tokenUrl === '' ? `${url}/oauth_token.do` : tokenUrl)}`,
    )
  }

  try {
    await $.fs.write(envPath, `${lines.join('\n')}\n`)
    const ignorePath = join(folder, '.gitignore')
    if (!(await $.fs.exists(ignorePath))) {
      await $.fs.write(ignorePath, '.env\n')
    }
  } catch {
    return say($, `Could not write to ${folder}. Check the folder for instances.`)
  }

  await closeForm($, '')
  await refresh($)
  await goTo($, 'list', name)
  await say($, `Created ${folder}. Press Open session to work on this instance.`)
  await runCheck($, { name, folder, url, auth, user })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'servicenow-instances',
      description: 'Add ServiceNow instances and test their connection',
    })

    return next(e)
  })

  on('command.run', { command: 'servicenow-instances' }, async $ => {
    await refresh($)
    await $.ui.open({ id: PANE, title: TITLE })

    return { text: 'ServiceNow instances pane opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Input, Text } = $.ui.resolve(e)
    const now = await read($, view)
    const dir = await read($, base)
    const keep = (field: string) => (value: string) => {
      draft[field] = value
    }
    const field = (name: string, label: string, placeholder: string) => (
      <Input
        key={`${name}:${now.gen}`}
        label={label}
        placeholder={placeholder}
        value={draft[name] ?? ''}
        onInput={keep(name)}
        onSubmit={keep(name)}
      />
    )
    const secret = (name: string, label: string) => (
      <Input
        key={`${name}:${now.gen}`}
        label={label}
        onInput={keep(name)}
        onSubmit={keep(name)}
      />
    )

    if (now.mode === 'form') {
      return (
        <Box flexDirection="column">
          <Text bold>
            New instance ({now.auth === 'oauth' ? 'OAuth' : 'basic auth'})
          </Text>
          <Text dimColor>Folder: {join(dir, '<name>')}</Text>
          {field('name', 'Name', 'sn-dev')}
          {field('url', 'Instance URL', 'https://dev12345.service-now.com')}
          {field('user', 'Username', 'admin')}
          {secret('password', 'Password')}
          {now.auth === 'oauth' && field('clientId', 'Client ID', '')}
          {now.auth === 'oauth' && secret('clientSecret', 'Client secret')}
          {now.auth === 'oauth' &&
            field('tokenUrl', 'Token URL', 'leave empty for /oauth_token.do')}
          <Text dimColor>
            The password shows as you type. It is written only to the .env in
            the instance folder, never to the conversation.
          </Text>
          {now.message !== '' && <Text color="red">{now.message}</Text>}
          <Box>
            <Button
              key="save"
              label="Create folder and .env"
              variant="primary"
              onPress={() => later($, () => save($))}
            />
            <Text> </Text>
            <Button
              key="cancel"
              label="Cancel"
              onPress={() => closeForm($, '')}
            />
          </Box>
        </Box>
      )
    }

    const all = await read($, instances)
    const results = await read($, checks)
    const engine = await read($, server)
    const asked = await read($, confirm)
    const where = await read($, nav)
    const typed = await read($, query)
    const message = now.message !== '' && <Text>{now.message}</Text>
    const serverLine = engine.text !== '' && (
      <Box
        flexDirection="column"
        borderStyle="round"
        borderColor={engine.status === 'installed' ? undefined : 'yellow'}
        borderDimColor={engine.status === 'installed'}
        paddingX={1}
      >
        <Text
          dimColor={engine.status === 'installed'}
          color={
            engine.status === 'missing' ||
            engine.status === 'no-python' ||
            engine.status === 'needs-setup'
              ? 'yellow'
              : undefined
          }
        >
          {engine.text}
        </Text>
        {engine.status === 'missing' && (
          <Box>
            <Button
              key="install"
              label="Install server"
              variant="primary"
              onPress={() => later($, () => install($))}
            />
          </Box>
        )}
        {engine.status === 'needs-setup' && (
          <Box>
            <Button
              key="finish-setup"
              label="Finish setup"
              variant="primary"
              onPress={() => later($, () => finishSetup($))}
            />
          </Box>
        )}
      </Box>
    )

    if (where.screen === 'settings') {
      return (
        <Box flexDirection="column" gap={1}>
          <Box>
            <Button
              key="back"
              label="‹ All instances"
              onPress={() => goTo($, 'list', '')}
            />
          </Box>
          <Text bold>Settings</Text>
          {serverLine}
          <Box flexDirection="column">
            <Input
              key="base"
              label="Folder for instances"
              value={dir}
              submitLabel="use"
              onSubmit={value => {
                const next = value.trim()
                if (next !== '') later($, () => useBase($, next))
              }}
            />
            <Text dimColor>
              Each instance is a folder here that holds its own .env.
            </Text>
          </Box>
          <Box>
            <Button key="refresh" label="Check again" onPress={() => later($, () => refresh($))} />
          </Box>
          {message}
        </Box>
      )
    }

    const needle = typed.trim().toLowerCase()
    const matching =
      needle === ''
        ? all
        : all.filter(one =>
            `${one.name} ${hostOf(one.url)}`.toLowerCase().includes(needle),
          )
    const shown = matching.slice(0, MAX_ROWS)
    // A few instances show whole; a long list shows one line each, and the one
    // pressed opens.
    const isShort = shown.length <= OPEN_CARDS
    const count =
      matching.length === all.length
        ? `${all.length} ${all.length === 1 ? 'instance' : 'instances'}`
        : `${matching.length} of ${all.length} instances`

    return (
      <Box flexDirection="column" gap={1}>
        {engine.status !== 'installed' && serverLine}
        <Box gap={1} flexWrap="wrap">
          <Button
            key="add-basic"
            label="+ Add instance"
            variant="primary"
            onPress={() => openForm($, 'basic')}
          />
          <Button
            key="add-oauth"
            label="+ Add with OAuth"
            onPress={() => openForm($, 'oauth')}
          />
          {all.length > 0 && (
            <Button
              key="test-all"
              label="Test all"
              onPress={() => later($, () => testAll($, shown))}
            />
          )}
          <Button key="refresh" label="Refresh" onPress={() => later($, () => refresh($))} />
          <Button
            key="settings"
            label="Settings"
            onPress={() => goTo($, 'settings', '')}
          />
        </Box>
        {message}
        {all.length > OPEN_CARDS && (
          <Input
            key="search"
            label="Search"
            placeholder="name or host"
            value={typed}
            submitLabel="filter"
            onInput={value => update($, query, () => value)}
            onSubmit={value => update($, query, () => value)}
          />
        )}
        {all.length === 0 && (
          <Text dimColor>
            No instance folders in {dir} yet. Press Add instance to create the
            first one.
          </Text>
        )}
        {all.length > 0 && matching.length === 0 && (
          <Text dimColor>No instance matches "{typed.trim()}".</Text>
        )}
        {shown.map(one => {
          const result = results[one.name]
          const tag = tagOf(result)
          const isOpen = isShort || where.name === one.name
          const isAsking = asked === one.name
          const chevron = isShort ? '' : isOpen ? '▾ ' : '▸ '

          return (
            <Box
              flexDirection="column"
              borderStyle="round"
              borderColor={isAsking ? 'yellow' : undefined}
              borderDimColor={!isAsking}
              paddingX={1}
            >
              <Box justifyContent="space-between" gap={1}>
                <Button
                  key={`row:${one.name}`}
                  plain
                  onPress={() => toggle($, one.name)}
                >
                  {chevron}
                  <Text color={colorOf(result)} dimColor={result === undefined}>
                    {markOf(result)}
                  </Text>{' '}
                  <Text bold>{one.name}</Text>
                  {'   '}
                  <Text color={tagColorOf(tag)} dimColor={tagColorOf(tag) === undefined}>
                    {tag}
                  </Text>
                  {isOpen ? '' : '   '}
                  {!isOpen && <Text dimColor>{hostOf(one.url)}</Text>}
                </Button>
                {!isOpen && (
                  <Button
                    key={`open:${one.name}`}
                    label="Open session"
                    onPress={() => openSession($, one)}
                  />
                )}
              </Box>
              {isOpen && (
                <Box flexDirection="column">
                  <Text dimColor>
                    {hostOf(one.url)} · {one.user} ·{' '}
                    {one.auth === 'oauth' ? 'OAuth' : 'basic auth'}
                  </Text>
                  <Text dimColor>{one.folder}</Text>
                  {result !== undefined && (
                    <Text dimColor={result.isRunning} color={colorOf(result)}>
                      {result.text}
                    </Text>
                  )}
                  <Box gap={1} marginTop={1}>
                    <Button
                      key={`open:${one.name}`}
                      label="Open session"
                      variant="primary"
                      onPress={() => openSession($, one)}
                    />
                    <Button
                      key={`test:${one.name}`}
                      label="Test connection"
                      onPress={() => later($, () => runCheck($, one))}
                    />
                    {result?.allowHost !== undefined && !isAsking && (
                      <Button
                        key={`allow:${one.name}`}
                        label="Allow writes"
                        onPress={() => update($, confirm, () => one.name)}
                      />
                    )}
                  </Box>
                  {result?.allowHost !== undefined && isAsking && (
                    <Box flexDirection="column" marginTop={1}>
                      <Text color="yellow">
                        Claude will be able to create and update records on{' '}
                        {result.allowHost}. Only allow this on a development,
                        test or sandbox instance, never production.
                      </Text>
                      <Box gap={1}>
                        <Button
                          key={`allow-yes:${one.name}`}
                          label="It is not production: allow writes"
                          onPress={() =>
                            later($, () => allowWrites($, one, result.allowHost ?? ''))
                          }
                        />
                        <Button
                          key={`allow-no:${one.name}`}
                          label="Cancel"
                          onPress={() => update($, confirm, () => '')}
                        />
                      </Box>
                    </Box>
                  )}
                </Box>
              )}
            </Box>
          )
        })}
        {all.length > 0 && (
          <Text dimColor>
            {count}
            {matching.length > shown.length
              ? `, showing the first ${shown.length}: search to narrow the list`
              : ''}
            {isShort ? '.' : '. Press a name to open its card.'}
          </Text>
        )}
      </Box>
    )
  })
}
