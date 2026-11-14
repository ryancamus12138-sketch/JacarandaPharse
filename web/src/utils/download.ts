/**
 * Hand an in-memory file to the browser as a download, like clicking a regular download link; it is saved to
 * the downloads folder.
 *
 * Why not <a href="/api/...xlsx">? Browsers do not send the login token when following links. The file is
 * therefore fetched with the token first, then saved through a temporary link.
 */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Give the browser time to start the download before releasing the memory.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
