import { marked } from 'marked'

marked.setOptions({
  gfm: true,
  breaks: false,
})

export function renderMarkdown(src: string): string {
  return marked.parse(src, { async: false }) as string
}

export function renderStreamingMarkdown(src: string): string {
  return marked.parse(src + '<span class="streaming-cursor"></span>', { async: false }) as string
}
