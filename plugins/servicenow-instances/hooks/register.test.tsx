import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'servicenow-instances',
  component: 'Pane',
  requestId: 'servicenow-instances',
  props: {
    title: 'ServiceNow instances',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const BASE = 'C:\\Users\\t\\ServiceNow'
const ENV = `${BASE}\\sn-dev\\.env`
const PASSWORD = 'p#ss "w" \\1 ${X}'

const ran = (exitCode: number, stdout: string, stderr = '') => ({
  value: {
    exitCode,
    stdout,
    stderr,
    isStdoutTruncated: false,
    isStderrTruncated: false,
  },
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`adding an instance writes its folder and .env, then tests it (${surface})`, async ($, on) => {
    // Each hook here stands for the engine: a file system held in memory.
    const written: Record<string, string> = {}
    let argv: readonly string[] = []
    on('store.get', () => ({ value: undefined }))
    on('store.set', () => ({ value: undefined }))
    on('session.root', () => ({ value: 'D:\\work\\project' }))
    on('env.get', (_, e) => ({
      value: e.name === 'USERPROFILE' ? 'C:\\Users\\t' : undefined,
    }))
    on('fs.list', (_, e) => ({
      value:
        e.path === BASE && ENV in written
          ? [{ name: 'sn-dev', kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false }]
          : [],
    }))
    on('fs.exists', (_, e) => ({ value: e.path in written }))
    on('fs.read', (_, e) => ({ value: written[e.path] ?? '' }))
    on('fs.write', (_, e) => {
      written[e.path] = e.text

      return { value: undefined }
    })
    on('process.run', (_, e) => {
      if (e.argv[1] === '-c') return ran(0, '0.3.1\n')
      argv = e.argv

      return ran(
        0,
        `${JSON.stringify({ ok: true, user: 'admin', host: 'dev12345.service-now.com', access_mode: 'write' })}\n`,
      )
    })

    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })
    expect(await ui.find({ type: 'Text', text: /No instance folders/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /servicenow-mcp 0\.3\.1 is installed/ })).toBeDefined()
    // An installed server is never offered for reinstalling.
    expect(await ui.find({ key: 'install' })).toBeUndefined()

    // A plain http address is refused and nothing is written.
    await ui.press({ key: 'add-basic' })
    await ui.input({ key: 'name:1', text: 'sn-dev', kind: 'change' })
    await ui.input({ key: 'url:1', text: 'http://dev12345.service-now.com', kind: 'change' })
    await ui.press({ key: 'save' })
    expect(await ui.find({ type: 'Text', text: /https address/ })).toBeDefined()
    expect(Object.keys(written).length).toBe(0)

    await ui.input({ key: 'url:1', text: 'dev12345.service-now.com/', kind: 'change' })
    await ui.input({ key: 'user:1', text: 'admin', kind: 'change' })
    await ui.input({ key: 'password:1', text: PASSWORD, kind: 'change' })
    await ui.press({ key: 'save' })

    expect(written[ENV]).toBe(
      [
        'SERVICENOW_INSTANCE_URL=https://dev12345.service-now.com',
        'SERVICENOW_AUTH_TYPE=basic',
        'SERVICENOW_USERNAME=admin',
        'SERVICENOW_PASSWORD="p#ss \\"w\\" \\\\1 ${X}"',
        '',
      ].join('\n'),
    )
    expect(written[`${BASE}\\sn-dev\\.gitignore`]).toBe('.env\n')
    expect(argv.slice(1)).toEqual(['-m', 'servicenow_mcp.doctor', '--env-file', ENV])
    expect(await ui.find({ type: 'Text', text: /Connected as admin/ })).toBeDefined()
    // The password is drawn nowhere once saved.
    expect(await ui.find({ type: 'Text', text: /p#ss/ })).toBeUndefined()

    // The same name again is refused: an existing .env is never overwritten.
    await ui.press({ key: 'add-basic' })
    await ui.input({ key: 'name:3', text: 'sn-dev', kind: 'change' })
    await ui.input({ key: 'url:3', text: 'https://other.service-now.com', kind: 'change' })
    await ui.input({ key: 'user:3', text: 'x', kind: 'change' })
    await ui.input({ key: 'password:3', text: 'y', kind: 'change' })
    await ui.press({ key: 'save' })
    expect(await ui.find({ type: 'Text', text: /already has a \.env/ })).toBeDefined()
    expect(written[ENV]).toContain('dev12345')
    await ui.unmount()
  })

  test(`a missing server is installed on the person's press (${surface})`, async ($, on) => {
    const runs: (readonly string[])[] = []
    let isInstalled = false
    on('store.get', () => ({ value: BASE }))
    on('fs.list', () => ({ value: [] }))
    on('process.run', (_, e) => {
      runs.push(e.argv)
      // `python` is absent here; `python3` has no servicenow_mcp until pip ran.
      if (e.argv[0] !== 'python3') return ran(9009, '', 'Python was not found')
      if (e.argv[1] === '-c') {
        return isInstalled
          ? ran(0, '0.3.1\n')
          : ran(1, '', "ModuleNotFoundError: No module named 'servicenow_mcp'")
      }
      if (e.argv[2] === 'pip') isInstalled = true

      return ran(0, '')
    })

    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })
    expect(await ui.find({ type: 'Text', text: /not installed yet/ })).toBeDefined()
    // Looking installs nothing.
    expect(runs.every(argv => argv[1] === '-c')).toBe(true)

    await ui.press({ key: 'install' })
    const acts = runs.filter(argv => argv[1] === '-m').map(argv => argv.slice(0, 4).join(' '))
    expect(acts).toEqual([
      'python3 -m pip install',
      'python3 -m servicenow_mcp.setup --quick',
    ])
    expect(await ui.find({ type: 'Text', text: /0\.3\.1 is installed\. Restart Claude Code/ })).toBeDefined()
    expect(await ui.find({ key: 'install' })).toBeUndefined()
    await ui.unmount()
  })
}
