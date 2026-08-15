export function uid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

export function formatUserMessage(text: string): string {
  const prefix = '[codex-launch]'
  if (!text.toLowerCase().startsWith(prefix)) return text
  try {
    const payload = JSON.parse(text.slice(prefix.length).trim()) as Record<string, unknown>
    const project = typeof payload.project_name === 'string' ? payload.project_name : 'new project'
    const brief = typeof payload.brief === 'string' ? payload.brief : 'Start an interactive build session.'
    const model = typeof payload.model === 'string' && payload.model ? ` using ${payload.model}` : ''
    return `Open Codex for ${project}${model}.\n\n${brief}`
  } catch {
    return text
  }
}

export function formatToolArgs(args: string | Record<string, unknown>): string {
  if (typeof args === 'object' && args !== null) {
    return Object.entries(args)
      .map(([k, v]) => `${k.charAt(0).toUpperCase() + k.slice(1)}: ${v}`)
      .join('\n')
  }
  return String(args)
}

export function parseToolResult(data: {
  result?: string
  output?: unknown
  error?: unknown
  [key: string]: unknown
}): { text: string; isError: boolean } {
  const isError = data.success === false

  let resultStr = ''

  if (typeof data.result === 'string') {
    try {
      const parsed = JSON.parse(data.result)
      if (parsed.output !== undefined) data.output = parsed.output
      if (parsed.error !== undefined) data.error = parsed.error
    } catch {
      data.output = data.result
    }
  }

  if (data.output !== undefined) {
    if (Array.isArray(data.output)) {
      resultStr = data.output.join('\n')
    } else if (typeof data.output === 'object' && data.output !== null) {
      resultStr = JSON.stringify(data.output, null, 2)
    } else {
      resultStr = String(data.output)
    }
  } else if (data.error !== undefined) {
    resultStr = String(data.error)
  } else {
    const cleanObj: Record<string, unknown> = {}
    for (const k in data) {
      if (k !== 'tool' && k !== 'success' && k !== 'result') {
        cleanObj[k] = data[k]
      }
    }
    resultStr =
      Object.keys(cleanObj).length > 0
        ? Object.entries(cleanObj)
            .map(([k, v]) => `${k.charAt(0).toUpperCase() + k.slice(1)}: ${v}`)
            .join('\n')
        : 'Done'
  }

  return { text: resultStr, isError }
}
