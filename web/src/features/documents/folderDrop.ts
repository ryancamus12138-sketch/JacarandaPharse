/** Folders dropped onto the upload drop area. */

/** A file found inside a folder dropped onto the drop area, with the folder it was in. */
export interface DroppedFile {
  file: File
  /** "trip/hotel" for trip/hotel/a.pdf; empty for a file at the top of the dropped folder's parent. */
  folder: string
}

/**
 * Files in the folders dropped onto the drop area, at every depth. The browser hands a dropped folder over as
 * an entry to be read (File and Directory Entries API): readEntries returns the contents in batches, so it is
 * called until it returns none. Same approach as Ant Design's own folder upload (traverseFileTree).
 */
export async function filesInFolders(folders: FileSystemDirectoryEntry[]): Promise<DroppedFile[]> {
  const found: DroppedFile[] = []
  const pending: FileSystemEntry[] = [...folders]
  while (pending.length > 0) {
    const entry = pending.shift()!
    if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve) => reader.readEntries(resolve, () => resolve([])))
        if (batch.length === 0) break
        pending.push(...batch)
      }
    } else if (entry.isFile) {
      const file = await new Promise<File | null>((resolve) =>
        (entry as FileSystemFileEntry).file(resolve, () => resolve(null)),
      )
      if (file) {
        // fullPath is "/trip/hotel/a.pdf" from the dropped folder's parent.
        const folder = entry.fullPath.replace(/^\//, '').split('/').slice(0, -1).join('/')
        found.push({ file, folder })
      }
    }
  }
  return found
}
