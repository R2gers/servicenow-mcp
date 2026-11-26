import { expect, mock, test } from 'claude-code/testing'

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
    // Slow work runs from a timer: the test lets it run after each press.
    const clock = mock.clock(on)
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
    const press = async (key: string) => {
      await ui.press({ key })
      await clock.settle()
    }
    await press('refresh')
    expect(await ui.find({ type: 'Text', text: /No instance folders/ })).toBeDefined()
    // An installed server takes no room on the list and is never offered for reinstalling.
    expect(await ui.find({ type: 'Text', text: /is installed/ })).toBeUndefined()
    expect(await ui.find({ key: 'install' })).toBeUndefined()
    await press('settings')
    expect(await ui.find({ type: 'Text', text: /servicenow-mcp 0\.3\.1 is installed/ })).toBeDefined()
    expect(await ui.find({ key: 'install' })).toBeUndefined()
    await press('back')

    // A plain http address is refused and nothing is written.
    await press('add-basic')
    await ui.input({ key: 'name:1', text: 'sn-dev', kind: 'change' })
    await ui.input({ key: 'url:1', text: 'http://dev12345.service-now.com', kind: 'change' })
    await press('save')
    expect(await ui.find({ type: 'Text', text: /https address/ })).toBeDefined()
    expect(Object.keys(written).length).toBe(0)

    await ui.input({ key: 'url:1', text: 'dev12345.service-now.com/', kind: 'change' })
    await ui.input({ key: 'user:1', text: 'admin', kind: 'change' })
    await ui.input({ key: 'password:1', text: PASSWORD, kind: 'change' })
    await press('save')

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

    // Saving lands on the list, the new instance a whole card with its actions on it.
    expect(await ui.find({ key: 'open:sn-dev' })).toBeDefined()
    expect(await ui.find({ key: 'test:sn-dev' })).toBeDefined()
    expect((await ui.find({ key: 'row:sn-dev' }))?.text).toContain('writable')

    // The same name again is refused: an existing .env is never overwritten.
    await press('add-basic')
    await ui.input({ key: 'name:3', text: 'sn-dev', kind: 'change' })
    await ui.input({ key: 'url:3', text: 'https://other.service-now.com', kind: 'change' })
    await ui.input({ key: 'user:3', text: 'x', kind: 'change' })
    await ui.input({ key: 'password:3', text: 'y', kind: 'change' })
    await press('save')
    expect(await ui.find({ type: 'Text', text: /already has a \.env/ })).toBeDefined()
    expect(written[ENV]).toContain('dev12345')
    await ui.unmount()
  })

  test(`a missing server is installed on the person's press (${surface})`, async ($, on) => {
    // Slow work runs from a timer: the test lets it run after each press.
    const clock = mock.clock(on)
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
    const press = async (key: string) => {
      await ui.press({ key })
      await clock.settle()
    }
    await press('refresh')
    expect(await ui.find({ type: 'Text', text: /not installed yet/ })).toBeDefined()
    // Looking installs nothing.
    expect(runs.every(argv => argv[1] === '-c')).toBe(true)

    await press('install')
    const acts = runs.filter(argv => argv[1] === '-m').map(argv => argv.slice(0, 4).join(' '))
    expect(acts).toEqual([
      'python3 -m pip install',
      'python3 -m servicenow_mcp.setup --quick',
    ])
    expect(await ui.find({ type: 'Text', text: /Server installed\. Restart Claude Code/ })).toBeDefined()
    expect(await ui.find({ key: 'install' })).toBeUndefined()
    await ui.unmount()
  })

  test(`writes are allowed only after the person confirms (${surface})`, async ($, on) => {
    // Slow work runs from a timer: the test lets it run after each press.
    const clock = mock.clock(on)
    const runs: (readonly string[])[] = []
    let isAllowed = false
    on('store.get', () => ({ value: BASE }))
    on('fs.list', () => ({
      value: [{ name: 'sn-dev', kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false }],
    }))
    on('fs.exists', () => ({ value: true }))
    on('fs.read', () => ({
      value: 'SERVICENOW_INSTANCE_URL=https://acmedev.service-now.com\nSERVICENOW_USERNAME=dev\n',
    }))
    on('process.run', (_, e) => {
      runs.push(e.argv)
      if (e.argv[1] === '-c') return ran(0, '0.3.1\n')
      if (e.argv[2] === 'servicenow_mcp.setup') {
        isAllowed = true

        return ran(0, '  acmedev.service-now.com approved for writes\n')
      }

      return ran(
        0,
        JSON.stringify({
          ok: true,
          user: 'dev',
          host: 'acmedev.service-now.com',
          access_mode: isAllowed ? 'write' : 'read_only',
        }),
      )
    })
    const approvals = () => runs.filter(argv => argv[2] === 'servicenow_mcp.setup')

    const ui = await $.ui.mount({ ...PANE, surface })
    const press = async (key: string) => {
      await ui.press({ key })
      await clock.settle()
    }
    await press('refresh')
    // Nothing offers to allow writes before the instance has been tested.
    expect(await ui.find({ key: 'allow:sn-dev' })).toBeUndefined()
    expect((await ui.find({ key: 'row:sn-dev' }))?.text).toContain('not tested')
    await press('row:sn-dev')
    expect(await ui.find({ key: 'allow:sn-dev' })).toBeUndefined()
    await press('test:sn-dev')
    expect(await ui.find({ type: 'Text', text: /Read-only for Claude/ })).toBeDefined()

    // The first press only asks; cancelling approves nothing.
    await press('allow:sn-dev')
    expect(await ui.find({ type: 'Text', text: /never production/ })).toBeDefined()
    expect(approvals().length).toBe(0)
    await press('allow-no:sn-dev')
    expect(approvals().length).toBe(0)
    expect(await ui.find({ key: 'allow-yes:sn-dev' })).toBeUndefined()

    await press('allow:sn-dev')
    await press('allow-yes:sn-dev')
    expect(approvals().map(argv => argv.slice(1))).toEqual([
      ['-m', 'servicenow_mcp.setup', '--allow-write', 'acmedev.service-now.com'],
    ])
    expect(await ui.find({ type: 'Text', text: /Claude may write to this instance/ })).toBeDefined()
    // Once writable, the button is gone.
    expect(await ui.find({ key: 'allow:sn-dev' })).toBeUndefined()
    await ui.unmount()
  })

  test(`Open session starts one in the instance's folder (${surface})`, async ($, on) => {
    // Slow work runs from a timer: the test lets it run after each press.
    const clock = mock.clock(on)
    const calls: { server: string; tool: string; args: Record<string, unknown> }[] = []
    let isDesktopApp = true
    const prompts: string[] = []
    const stored: Record<string, unknown> = {}
    on('store.get', () => ({ value: BASE }))
    on('store.set', (_, e) => {
      stored[e.key] = e.value

      return { value: undefined }
    })
    let submitted = 0
    on('prompt.submit', (_, e) => {
      submitted += 1

      return { text: e.text }
    })
    on('prompt.read', () => ({ value: { text: '', cursor: 0 } }))
    on('prompt.fill', (_, e) => {
      prompts.push(e.text)

      return { isFilled: true, text: e.text, cursor: e.text.length }
    })
    on('fs.list', () => ({
      value: [{ name: 'sn-dev', kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false }],
    }))
    on('fs.exists', () => ({ value: true }))
    on('fs.read', () => ({
      value: 'SERVICENOW_INSTANCE_URL=https://acmedev.service-now.com\nSERVICENOW_USERNAME=dev\n',
    }))
    on('process.run', () => ran(0, '0.3.1\n'))
    on('mcp.call', (_, e) => {
      calls.push(e)

      return {
        value: isDesktopApp
          ? { content: [{ type: 'text' as const, text: 'started' }], isError: false }
          : { content: [{ type: 'text' as const, text: 'No such server' }], isError: true },
      }
    })

    const ui = await $.ui.mount({ ...PANE, surface })
    const press = async (key: string) => {
      await ui.press({ key })
      await clock.settle()
    }
    await press('refresh')
    await press('open:sn-dev')
    expect(calls.length).toBe(1)
    expect(calls[0]?.server).toBe('ccd_session')
    expect(calls[0]?.tool).toBe('start_session')
    expect(calls[0]?.args['cwd']).toBe(`${BASE}\\sn-dev`)
    expect(calls[0]?.args['initiation']).toBe('user_asked')
    expect(String(calls[0]?.args['background']).length).toBeGreaterThan(80)
    expect(await ui.find({ type: 'Text', text: /Opened a session for sn-dev/ })).toBeDefined()

    expect(prompts.length).toBe(0)

    // Refused: the request is written into the prompt box for the person to
    // send themself, never submitted for them, and the refusal is kept.
    isDesktopApp = false
    await press('open:sn-dev')
    expect(prompts.length).toBe(1)
    expect(prompts[0]).toContain(`${BASE}\\sn-dev`)
    expect(prompts[0]).toContain('ServiceNow: sn-dev')
    expect(submitted).toBe(0)
    expect(stored['lastOpenRefusal']).toBe('No such server')
    expect(await ui.find({ type: 'Text', text: /is in the chat box: press Enter/ })).toBeDefined()
    await ui.unmount()
  })

  test(`a long list is capped and searched (${surface})`, async ($, on) => {
    // Slow work runs from a timer: the test lets it run after each press.
    const clock = mock.clock(on)
    const names = Array.from({ length: 45 }, (_, i) => `inst-${String(i + 1).padStart(2, '0')}`)
    const tested: string[] = []
    on('store.get', () => ({ value: BASE }))
    on('fs.list', () => ({
      value: names.map(name => ({ name, kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false })),
    }))
    on('fs.exists', () => ({ value: true }))
    on('fs.read', (_, e) => {
      const name = names.find(one => e.path.includes(one)) ?? 'none'

      return { value: `SERVICENOW_INSTANCE_URL=https://${name}.service-now.com\nSERVICENOW_USERNAME=dev\n` }
    })
    on('process.run', (_, e) => {
      if (e.argv[1] === '-c') return ran(0, '0.3.1\n')
      tested.push(e.argv[e.argv.length - 1] ?? '')

      return ran(1, JSON.stringify({ ok: false, error: 'ServiceNow returned HTTP 401' }))
    })

    const ui = await $.ui.mount({ ...PANE, surface })
    const press = async (key: string) => {
      await ui.press({ key })
      await clock.settle()
    }
    await press('refresh')
    // One short row each, the first forty: no buttons or folders per instance.
    expect(await ui.find({ key: 'row:inst-40' })).toBeDefined()
    expect(await ui.find({ key: 'row:inst-41' })).toBeUndefined()
    expect(await ui.find({ key: 'test:inst-01' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /45 instances, showing the first 40/ })).toBeDefined()

    // Pressing a name opens that one card with its actions; pressing it again closes it.
    await press('row:inst-02')
    expect(await ui.find({ key: 'test:inst-02' })).toBeDefined()
    expect(await ui.find({ key: 'test:inst-01' })).toBeUndefined()
    await press('row:inst-02')
    expect(await ui.find({ key: 'test:inst-02' })).toBeUndefined()

    await ui.input({ key: 'search', text: 'INST-4', kind: 'change' })
    expect(await ui.find({ key: 'row:inst-45' })).toBeDefined()
    expect(await ui.find({ key: 'row:inst-01' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /6 of 45 instances/ })).toBeDefined()

    // Test all tests what the search shows, and each row then says how it went.
    await press('test-all')
    expect(tested.length).toBe(6)
    expect((await ui.find({ key: 'row:inst-45' }))?.text).toContain('not connected')

    await ui.input({ key: 'search', text: 'nothing-like-this', kind: 'change' })
    expect(await ui.find({ type: 'Text', text: /No instance matches/ })).toBeDefined()
    await ui.unmount()
  })

  test(`an incomplete setup is finished on the person's press (${surface})`, async ($, on) => {
    // Slow work runs from a timer: the test lets it run after each press.
    const clock = mock.clock(on)
    const runs: (readonly string[])[] = []
    let isSetUp = false
    on('store.get', () => ({ value: BASE }))
    on('fs.list', () => ({ value: [] }))
    on('process.run', (_, e) => {
      runs.push(e.argv)
      if (e.argv[1] === '-c' && String(e.argv[2]).includes('setup.check')) {
        const problems = isSetUp ? [] : ['An old review hook from an earlier version is still installed.']

        return ran(0, JSON.stringify({ ok: isSetUp, problems }))
      }
      if (e.argv[1] === '-c') return ran(0, '0.3.2\n')
      if (e.argv[2] === 'servicenow_mcp.setup') isSetUp = true

      return ran(0, '')
    })

    const ui = await $.ui.mount({ ...PANE, surface })
    const press = async (key: string) => {
      await ui.press({ key })
      await clock.settle()
    }
    await press('refresh')
    expect(await ui.find({ type: 'Text', text: /Setup is incomplete\. An old review hook/ })).toBeDefined()
    // Looking changes nothing.
    expect(runs.every(argv => argv[1] === '-c')).toBe(true)

    await press('finish-setup')
    expect(runs.filter(argv => argv[1] === '-m').map(argv => argv.slice(1))).toEqual([
      ['-m', 'servicenow_mcp.setup', '--quick'],
    ])
    expect(await ui.find({ type: 'Text', text: /Setup finished/ })).toBeDefined()
    expect(await ui.find({ key: 'finish-setup' })).toBeUndefined()
    await ui.unmount()
  })
}
