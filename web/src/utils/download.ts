/**
 * 把内存里的文件交给浏览器"下载"，和点普通下载链接一样，会存到"下载"文件夹。
 *
 * 为什么不直接 <a href="/api/...xlsx">？浏览器点链接时不会带登录 token。
 * 所以先用 fetch（带 token）取回来，再造一个临时链接让浏览器保存。
 */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 给浏览器一点时间开始下载，再释放这块内存
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
