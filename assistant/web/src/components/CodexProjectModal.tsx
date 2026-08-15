import { useCallback, useEffect, useRef, useState, type FC, type FormEvent } from 'react'

interface Props {
  visible: boolean
  onClose: () => void
  onSend: (prompt: string) => void
}

interface CodexProjectPayload {
  projectName: string
  model: string
  brief: string
}

const PROJECTS_STORAGE_KEY = 'codexProjects'
const PROJECT_NAME_PATTERN = '[a-z0-9][a-z0-9-]{0,62}'

function savedProjects(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(PROJECTS_STORAGE_KEY) || '[]')
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

function rememberProject(name: string): void {
  const projects = [name, ...savedProjects().filter((item) => item !== name)].slice(0, 12)
  localStorage.setItem(PROJECTS_STORAGE_KEY, JSON.stringify(projects))
}

function buildLaunchPrompt(payload: CodexProjectPayload): string {
  return '[codex-launch]\n' + JSON.stringify({
    project_name: payload.projectName,
    model: payload.model,
    brief: payload.brief,
  })
}

function buildRefinePrompt(payload: CodexProjectPayload): string {
  return `I want to plan a Codex project named ${payload.projectName}. Model preference: ${payload.model || 'Codex default'}. Brief: ${payload.brief}\n\nAsk me concise questions only for material missing product, stack, or design decisions. Do not launch Codex yet.`
}

export const CodexProjectModal: FC<Props> = ({ visible, onClose, onSend }) => {
  const projects = savedProjects()
  const [projectName, setProjectName] = useState(projects[0] || '')
  const [model, setModel] = useState('')
  const [customModel, setCustomModel] = useState('')
  const [brief, setBrief] = useState('')
  const projectInputRef = useRef<HTMLInputElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  const close = useCallback(() => {
    onClose()
    window.setTimeout(() => previousFocusRef.current?.focus(), 0)
  }, [onClose])

  useEffect(() => {
    if (!visible) return
    previousFocusRef.current = document.activeElement as HTMLElement | null
    window.setTimeout(() => projectInputRef.current?.focus(), 0)

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [close, visible])

  const payload = useCallback((): CodexProjectPayload => ({
    projectName: projectName.trim().toLowerCase().replace(/_/g, '-'),
    model: model === 'custom' ? customModel.trim() : model,
    brief: brief.trim(),
  }), [brief, customModel, model, projectName])

  const handleRefine = useCallback(() => {
    const form = projectInputRef.current?.form
    if (!form?.reportValidity()) return
    const request = payload()
    close()
    onSend(buildRefinePrompt(request))
  }, [close, onSend, payload])

  const handleSubmit = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const request = payload()
    rememberProject(request.projectName)
    close()
    onSend(buildLaunchPrompt(request))
  }, [close, onSend, payload])

  if (!visible) return null

  return (
    <div className="codex-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) close()
    }}>
      <section
        className="codex-modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="codex-modal-title"
      >
        <button className="close-logs-btn codex-close" type="button" onClick={close} aria-label="Close Codex project setup">
          ×
        </button>
        <p className="codex-kicker">PROJECT STUDIO</p>
        <h2 id="codex-modal-title">Build with Codex</h2>
        <p className="codex-intro">
          Shape the brief with Thursday, then open a visible Codex session in a contained workspace.
        </p>

        <form className="codex-form" onSubmit={handleSubmit}>
          <label className="codex-field">
            <span>Project workspace</span>
            <input
              ref={projectInputRef}
              list="codex-project-history"
              pattern={PROJECT_NAME_PATTERN}
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="habit-tracker"
              required
            />
          </label>
          <datalist id="codex-project-history">
            {projects.map((project) => <option key={project} value={project} />)}
          </datalist>

          <label className="codex-field">
            <span>Codex model</span>
            <select value={model} onChange={(event) => setModel(event.target.value)}>
              <option value="">Use my Codex default</option>
              <option value="gpt-5.6-terra">GPT-5.6 Terra</option>
              <option value="gpt-5.6-luna">GPT-5.6 Luna</option>
              <option value="gpt-5.6-sol">GPT-5.6 Sol</option>
              <option value="custom">Custom model identifier</option>
            </select>
          </label>

          {model === 'custom' && (
            <label className="codex-field">
              <span>Custom model identifier</span>
              <input
                value={customModel}
                onChange={(event) => setCustomModel(event.target.value)}
                pattern="[A-Za-z0-9._:-]{1,100}"
                placeholder="Model identifier"
                required
              />
            </label>
          )}

          <label className="codex-field">
            <span>What should Codex build or change?</span>
            <textarea
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              placeholder="A responsive habit tracker with daily check-ins, streaks, and a polished README…"
              required
            />
          </label>

          <div className="codex-note">
            Codex runs only inside <code>codex_workspace/&lt;project&gt;</code>. The Kitty terminal remains the source of truth for progress.
          </div>
          <div className="codex-actions">
            <button className="codex-action" type="button" onClick={handleRefine}>Refine with Thursday</button>
            <button className="codex-action primary" type="submit">Open Codex in Kitty</button>
          </div>
        </form>
      </section>
    </div>
  )
}

export default CodexProjectModal
