export type Instance = {
  name: string
  folder: string
  url: string
  auth: string
  user: string
}

// `allowHost` is set only for a connected instance that is read-only for Claude.
export type Check = {
  ok: boolean
  isRunning: boolean
  text: string
  allowHost?: string
  access?: 'write' | 'read-only'
}

// Which of the pane's screens shows, and for `detail` which instance.
export type Nav = { screen: 'list' | 'detail' | 'settings'; name: string }

export type View = {
  mode: 'list' | 'form'
  auth: 'basic' | 'oauth'
  gen: number
  message: string
}

export type Server = {
  status:
    | 'unknown'
    | 'no-python'
    | 'missing'
    | 'installing'
    | 'needs-setup'
    | 'installed'
  python: string
  text: string
}

declare module 'claude-code' {
  interface PluginState {
    'servicenow-instances': {
      server: Server
      confirm: string
      nav: Nav
      query: string
      base: string
      instances: Instance[]
      checks: Record<string, Check>
      view: View
    }
  }
}
