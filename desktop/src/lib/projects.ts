import type { Project, ProjectTarget } from '../types'

export function defaultProjectTarget(backend: string, projects: readonly Project[], emptyName: string): ProjectTarget {
  const project = projects.find(item => item.default) ?? projects[0]
  return project ? { backend, path: project.path, name: project.name } : { backend, path: '', name: emptyName }
}
