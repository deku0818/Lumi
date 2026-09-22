import { useRef, type RefObject } from 'react'

// 把每次渲染的最新值镜像到 ref：给必须保持稳定身份的回调（连接事件处理器、注册一次
// 的原生回调）读「当前」状态用，不把它们的依赖数组撑爆。
export function useLatest<T>(value: T): RefObject<T> {
  const ref = useRef(value)
  ref.current = value
  return ref
}
