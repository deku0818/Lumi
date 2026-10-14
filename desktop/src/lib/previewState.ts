import type { Artifact } from '../types.ts'

export type PreviewState = {
  owner: string
  files: Artifact[]
  activePath: string | null
  expanded: boolean
}

export const emptyPreviewState: PreviewState = {
  owner: '',
  files: [],
  activePath: null,
  expanded: false,
}

export type PreviewAction =
  | { type: 'open'; owner: string; file: Artifact }
  | { type: 'select' | 'close'; path: string }
  | { type: 'close-all' | 'toggle-expanded' | 'shrink' }

export function previewReducer(state: PreviewState, action: PreviewAction): PreviewState {
  switch (action.type) {
    case 'open': {
      const current = state.owner === action.owner ? state : emptyPreviewState
      const exists = current.files.some(file => file.path === action.file.path)
      let files: Artifact[]
      if (exists) {
        files = current.files.map(file => file.path === action.file.path ? action.file : file)
      } else {
        files = [...current.files, action.file]
      }
      return {
        ...current,
        owner: action.owner,
        activePath: action.file.path,
        files,
      }
    }
    case 'select': {
      const exists = state.files.some(file => file.path === action.path)
      if (!exists) return state
      return { ...state, activePath: action.path }
    }
    case 'close': {
      const index = state.files.findIndex(file => file.path === action.path)
      if (index < 0) return state
      const files = state.files.filter(file => file.path !== action.path)
      if (!files.length) return emptyPreviewState
      return {
        ...state,
        files,
        activePath: state.activePath === action.path
          ? files[Math.min(index, files.length - 1)].path : state.activePath,
      }
    }
    case 'close-all':
      return emptyPreviewState
    case 'toggle-expanded':
      if (!state.files.length) return state
      return { ...state, expanded: !state.expanded }
    case 'shrink':
      if (!state.expanded) return state
      return { ...state, expanded: false }
  }
}
