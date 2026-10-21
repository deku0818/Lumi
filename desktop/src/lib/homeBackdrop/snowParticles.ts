export type Snowflake = Readonly<{
  x: number; start: number; duration: number; phase: number; layer: number; drift: number
  scale: number; landAt: number; meltMode: 'early' | 'midair' | 'ground'
  meltStart: number; meltDuration: number
}>

export function createSnowflake(time: number, height: number, initial: boolean): Snowflake {
  const depth = Math.random()
  let layer = 2
  if (depth < .5) layer = 0
  else if (depth < .9) layer = 1
  const speed = [9, 19, 34][layer] * (.85 + Math.random() * .3)
  const duration = (height + 100) / speed
  const ground = height * (.81 + layer * .035 + Math.random() * .065)
  const landAt = (ground + 50) / (height + 100)
  const choice = Math.random()
  let meltMode: Snowflake['meltMode'] = 'ground'
  if (choice < .32) meltMode = 'early'
  else if (choice < .66) meltMode = 'midair'
  let meltStart: number
  if (meltMode === 'early') meltStart = 50 / speed + .5 + Math.random() * 4
  else if (meltMode === 'midair') meltStart = duration * (.35 + Math.random() * .25)
  else meltStart = duration * landAt + 2 + Math.random() * 5
  const meltDuration = 6 + Math.random() * 7
  return {
    x: Math.random(), start: time - (initial ? Math.random() * (meltStart + meltDuration) : 0),
    duration, phase: Math.random() * Math.PI * 2, layer,
    drift: (Math.random() - .5) * (layer + 1) * 2.4,
    scale: layer === 1 ? .15 + Math.random() * .1 : .32 + Math.random() * .18,
    landAt, meltMode, meltStart, meltDuration,
  }
}

export function snowflakeFrame(flake: Snowflake, time: number, width: number, height: number): { x: number; y: number; melt: number; motionAge: number; finished: boolean; settled: boolean } {
  const age = Math.max(0, time - flake.start)
  const landingTime = flake.duration * flake.landAt
  const motionAge = Math.min(age, landingTime)
  const sway = [9, 22, 34][flake.layer]
  return {
    x: flake.x * width + Math.sin(motionAge * .28 + flake.phase) * sway + motionAge * flake.drift,
    y: motionAge / flake.duration * (height + 100) - 50,
    melt: Math.max(0, Math.min(1, (age - flake.meltStart) / flake.meltDuration)),
    motionAge, finished: age >= flake.meltStart + flake.meltDuration, settled: age >= landingTime,
  }
}
