import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { ConversationSummary } from '@/types'

interface Props {
  open: boolean
  conversations: ConversationSummary[]
  activeId: number | null
  busy: boolean
  onClose: () => void
  onNew: () => void
  onSelect: (id: number) => void
  onRename: (id: number, title: string) => void
  onDelete: (id: number) => void
}

const listVariant = {
  hidden: { opacity: 0, x: -16 },
  show: { opacity: 1, x: 0, transition: { type: 'spring', stiffness: 400, damping: 32 } },
  exit: { opacity: 0, x: -16, transition: { duration: 0.15 } },
}

export default function Sidebar({
  open,
  conversations,
  activeId,
  busy,
  onClose,
  onNew,
  onSelect,
  onRename,
  onDelete,
}: Props) {
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')

  const startEdit = (c: ConversationSummary) => {
    setEditingId(c.id)
    setDraft(c.title)
  }

  const commitEdit = () => {
    if (editingId !== null && draft.trim()) {
      onRename(editingId, draft.trim())
    }
    setEditingId(null)
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="sidebar-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />
          <motion.aside
            className="sidebar"
            initial={{ x: '-100%' }}
            animate={{ x: 0 }}
            exit={{ x: '-100%' }}
            transition={{ type: 'spring', stiffness: 360, damping: 34 }}
          >
            <div className="sidebar-header">
              <span className="sidebar-title">Chats</span>
              <button className="sidebar-new-btn" onClick={onNew} disabled={busy}>
                + New
              </button>
            </div>
            <div className="sidebar-list">
              <AnimatePresence initial={false}>
                {conversations.map((c) => (
                  <motion.div
                    key={c.id}
                    variants={listVariant}
                    initial="hidden"
                    animate="show"
                    exit="exit"
                    layout
                    className={`sidebar-item${c.id === activeId ? ' active' : ''}`}
                    onClick={() => !busy && editingId !== c.id && onSelect(c.id)}
                  >
                    {editingId === c.id ? (
                      <input
                        className="sidebar-edit-input"
                        autoFocus
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onBlur={commitEdit}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') commitEdit()
                          if (e.key === 'Escape') setEditingId(null)
                        }}
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <>
                        <span className="sidebar-item-title">{c.title}</span>
                        <div className="sidebar-item-actions" onClick={(e) => e.stopPropagation()}>
                          <button
                            className="sidebar-icon-btn"
                            title="Rename"
                            disabled={busy}
                            onClick={() => startEdit(c)}
                          >
                            ✎
                          </button>
                          <button
                            className="sidebar-icon-btn danger"
                            title="Delete"
                            disabled={busy}
                            onClick={() => onDelete(c.id)}
                          >
                            ✕
                          </button>
                        </div>
                      </>
                    )}
                  </motion.div>
                ))}
              </AnimatePresence>
              {conversations.length === 0 && (
                <div className="sidebar-empty">No conversations yet.</div>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}
