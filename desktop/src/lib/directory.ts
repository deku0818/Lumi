export type DirectoryCrumb = { label: string; path: string }

// Keep the server's spelling/separators, including drive and UNC roots.
export function directoryCrumbs(path: string): DirectoryCrumb[] {
  if (!path) return []
  const root = path.match(/^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+[\\/]?|\/)/)?.[0]
  if (!root) return [{ label: path, path }]
  const crumbs = [{ label: root, path: root }]
  for (const match of path.slice(root.length).matchAll(/[^\\/]+/g)) {
    crumbs.push({ label: match[0], path: path.slice(0, root.length + match.index + match[0].length) })
  }
  return crumbs
}
