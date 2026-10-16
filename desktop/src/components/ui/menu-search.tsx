import { Search } from 'lucide-react'
import type { ComponentProps,JSX } from 'react'

type MenuSearchProps = ComponentProps<'input'>

export function MenuSearch(props: MenuSearchProps): JSX.Element {
  return (
    <div className="lumi-menu-search">
      <Search size={13} aria-hidden="true" />
      <input {...props} />
    </div>
  )
}
