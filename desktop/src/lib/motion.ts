// 四套已确认的质量-弹簧-阻尼参数，采样为原生 CSS easing，无额外动画运行库。
export const SPRINGS = {
  button: { stiffness: 600, damping: 30, mass: 1 },
  popover: { stiffness: 320, damping: 28, mass: 1 },
  feedback: { stiffness: 380, damping: 18, mass: 1 },
  panel: { stiffness: 1200, damping: 80, mass: 0.1 },
} as const

export function springEasing({ stiffness, damping, mass }: typeof SPRINGS[keyof typeof SPRINGS]): string {
  let position = 0
  let velocity = 0
  const samples = ['0']
  for (let step = 1; step <= 600; step++) {
    velocity += (-stiffness * (position - 1) - damping * velocity) / mass / 1000
    position += velocity / 1000
    if (step % 10 === 0) samples.push(position.toFixed(4))
  }
  samples[samples.length - 1] = '1'
  return `linear(${samples.join(',')})`
}

export function installMotionTokens(): void {
  for (const [name, preset] of Object.entries(SPRINGS)) {
    document.documentElement.style.setProperty(`--spring-${name}`, springEasing(preset))
  }
}
