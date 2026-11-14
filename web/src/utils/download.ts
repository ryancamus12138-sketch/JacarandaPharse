import { api, type DownloadedFile } from '../api/client'
import { isDesktop } from '../desktop'

/** Event announcing a file saved to the Downloads folder by the desktop app (see SavedNotice). */
export const SAVED_EVENT = 'jacara:saved'

export interface SavedFile {
  name: string
  path: string
}

/**
 * Save a downloaded file, like clicking a regular download link: it goes to the Downloads folder.
 *
 * Why not <a href="/api/...xlsx">? Browsers do not send the login token when following links. The file is
 * therefore fetched with the token first, then saved through a temporary link.
 *
 * The desktop app's web view does not save downloads (WKWebView ignores the download attribute unless the app
 * handles it), so there the service writes the file: files from the service are already saved
 * (file.savedTo), and files made in the page are sent to the service. Either way the page announces where the
 * file went.
 */
export async function saveFile(file: DownloadedFile): Promise<void> {
  if (file.savedTo !== undefined) {
    announce({ name: file.filename, path: file.savedTo })
    return
  }
  if (isDesktop()) {
    const form = new FormData()
    form.append('file', file.blob, file.filename)
    form.append('filename', file.filename)
    announce(await api<SavedFile>('/desktop/downloads', { method: 'POST', body: form }))
    return
  }
  const url = URL.createObjectURL(file.blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Give the browser time to start the download before releasing the memory.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

function announce(saved: SavedFile): void {
  window.dispatchEvent(new CustomEvent<SavedFile>(SAVED_EVENT, { detail: saved }))
}
