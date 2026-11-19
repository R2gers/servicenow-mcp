export type Instance = {
  name: string
  folder: string
  url: string
  auth: string
  user: string
}

export type Check = { ok: boolean; isRunning: boolean; text: string }

export type View = {
  mode: 'list' | 'form'
  auth: 'basic' | 'oauth'
  gen: number
  message: string
}

export type Server = {
  status: 'unknown' | 'no-python' | 'missing' | 'installing' | 'installed'
  python: string
  text: string
}

declare module 'claude-code' {
  interface PluginState {
    'servicenow-instances': {
      server: Server
      base: string
      instances: Instance[]
      checks: Record<string, Check>
      view: View
    }
  }
}
