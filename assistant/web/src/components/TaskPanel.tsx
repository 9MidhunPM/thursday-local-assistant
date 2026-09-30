import { useState } from 'react'
import type { TaskState } from '@/types'

export default function TaskPanel({ task, busy, onMemory, onTask }: {
  task: TaskState | null; busy: boolean; onMemory: () => void
  onTask: (task: TaskState | null) => void
}) {
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [expanded, setExpanded] = useState(false)
  async function act(action: 'cancel' | 'resume') {
    if (!task || pending) return
    setPending(true)
    setError('')
    try {
      const response = await fetch(`/api/tasks/${task.id}/${action}`, { method: 'POST' })
      if (!response.ok) throw new Error((await response.json()).error || 'Task action failed')
      const data = await response.json()
      const next = await fetch(`/api/tasks/${data.task_id || task.id}`).then((r) => r.json())
      onTask(next.task)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Task action failed')
    } finally {
      setPending(false)
    }
  }
  const active = task && ['running', 'awaiting_approval'].includes(task.status)
  const resumable = task && ['paused', 'cancelled', 'interrupted', 'failed'].includes(task.status)
  return <section className="task-panel" aria-label="Task controls">
    <div className="task-toolbar">
      {task ? <button className="task-summary" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <span className={`task-dot ${active ? 'active' : ''}`} />
        <span>{active ? `Step ${task.step}` : task.status === 'completed' ? 'Finished' : task.status}</span>
        <span className="task-detail">{task.detail}</span>
      </button> : <span className="task-detail">Ready to help across your desktop</span>}
      {active && <button className="task-stop" disabled={pending} onClick={() => void act('cancel')}>Stop task</button>}
      {resumable && <button disabled={pending || busy} onClick={() => void act('resume')}>Resume task</button>}
      <button onClick={onMemory}>Memory</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {task && expanded && <div className="task-evidence">
      <p className="task-goal">{task.goal}</p>
      <p>{task.usage.prompt_tokens ?? 0} input · {task.usage.completion_tokens ?? 0} output tokens
        {task.usage.estimated_usd != null && ` · ~$${task.usage.estimated_usd.toFixed(5)}`}</p>
      <ol>{task.outcomes.map((item, index) => <li key={index}>
        <span>{item.success ? '✓' : '!'}</span> {item.tool} · {item.verification || (item.success ? 'Tool returned successfully' : 'Needs review')}
      </li>)}</ol>
      {task.preview && <img className="task-capture" src={task.preview} alt="Latest desktop evidence captured during this task" />}
      <small>Captures expire after five minutes. A delivered action may still need verification.</small>
    </div>}
  </section>
}
