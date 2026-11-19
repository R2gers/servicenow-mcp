import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Check, Instance, Server, View } from '../types'

const PANE = 'servicenow-instances'
const TITLE = 'ServiceNow instances'
// An archive, so installing needs Python alone and no git.
const PACKAGE =
  'https://github.com/R2gers/servicenow-mcp/archive/refs/heads/master.zip'
const VERSION_CODE = 'import servicenow_mcp; print(servicenow_mcp.__version__)'
const PYTHONS = ['python', 'python3', 'py']
const NO_INSTANCES: Instance[] = []
const NO_CHECKS: Record<string, Check> = {}
const LIST_VIEW: View = { mode: 'list', auth: 'basic', gen: 0, message: '' }
const UNKNOWN_SERVER: Server = { status: 'unknown', python: '', text: '' }

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

const findServer = async ($: EngineInterface): Promise<Server> => {
  let python = ''

  for (const exe of PYTHONS) {
    const ran = await $.process
      .run([exe, '-c', VERSION_CODE], { timeoutMs: 20000 })
      .catch(() => undefined)
    if (ran === undefined) continue
    const version = ran.stdout.trim()
    if (ran.exitCode === 0 && /^\d+(\.\d+)+$/.test(version)) {
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
}

const refresh = async ($: EngineInterface) => {
  const dir = await findBase($)
  await update($, base, () => dir)
  const found = await scan($, dir)
  await update($, instances, () => found)
  const { status } = await read($, server)
  if (status === 'installing') return
  const current = await findServer($)
  await update($, server, () => current)
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
    const error = (report.error ?? 'The check failed.').slice(0, 400)

    return { ok: false, isRunning: false, text: `Not connected: ${error}` }
  }
  const who = `Connected as ${report.user ?? 'the API user'}.`
  const access =
    report.access_mode === 'write'
      ? 'Claude may write to this instance.'
      : `Read-only for Claude. To allow writes on a NON-production instance, run in your own terminal: servicenow-mcp-setup --allow-write ${report.host ?? '<host>'}`

  return { ok: true, isRunning: false, text: `${who} ${access}` }
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

  await closeForm(
    $,
    `Created ${folder}. Start a new session in that folder to work on this instance.`,
  )
  await refresh($)
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
              onPress={() => save($)}
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

    const list = await read($, instances)
    const results = await read($, checks)
    const engine = await read($, server)

    return (
      <Box flexDirection="column">
        {engine.text !== '' && (
          <Box>
            <Text
              dimColor={engine.status === 'installed'}
              color={
                engine.status === 'missing' || engine.status === 'no-python'
                  ? 'yellow'
                  : undefined
              }
            >
              {engine.text}{' '}
            </Text>
            {engine.status === 'missing' && (
              <Button
                key="install"
                label="Install server"
                variant="primary"
                onPress={() => install($)}
              />
            )}
          </Box>
        )}
        <Input
          key="base"
          label="Folder for instances"
          value={dir}
          submitLabel="use"
          onSubmit={async value => {
            const next = value.trim()
            if (next === '') return
            await $.store.set('base', next)
            await update($, base, () => next)
            await refresh($)
          }}
        />
        <Box>
          <Button
            key="add-basic"
            label="Add instance"
            variant="primary"
            onPress={() => openForm($, 'basic')}
          />
          <Text> </Text>
          <Button
            key="add-oauth"
            label="Add with OAuth"
            onPress={() => openForm($, 'oauth')}
          />
          <Text> </Text>
          <Button key="refresh" label="Refresh" onPress={() => refresh($)} />
        </Box>
        {now.message !== '' && <Text>{now.message}</Text>}
        {list.length === 0 && (
          <Text dimColor>No instance folders here yet.</Text>
        )}
        {list.map(one => {
          const result = results[one.name]

          return (
            <Box flexDirection="column">
              <Text> </Text>
              <Box>
                <Text bold>{one.name} </Text>
                <Button
                  key={`test:${one.name}`}
                  label="Test connection"
                  onPress={() => runCheck($, one)}
                />
              </Box>
              <Text dimColor>
                {one.url} ({one.auth}, {one.user})
              </Text>
              <Text dimColor>{one.folder}</Text>
              {result !== undefined && (
                <Text
                  dimColor={result.isRunning}
                  color={result.isRunning ? undefined : result.ok ? 'green' : 'red'}
                >
                  {result.text}
                </Text>
              )}
            </Box>
          )
        })}
      </Box>
    )
  })
}
