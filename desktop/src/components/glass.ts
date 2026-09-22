// Lumi Glass 卡壳材质：圆角随层级递减、描边 / 填充成套，别在组件里手写字面量。
// 四档的唯一真源——改观感只改这里，四个面板与设置页一起跟着走。
//
// L1 页面级大卡（项目主页资源卡、定时任务卡与横幅）：铺在 canvas 上，用 panel 底把
//    「一块内容」从页面里托起来；可点的卡各自追加 hover（材质本身不含交互态）。
// L2 设置页容器（实体卡、分组卡、体检卡、执行记录行）：框住一组内容。
// L3 内嵌小卡（审批卡、后台任务卡）：躺在 L2 里面，填充比 L2 更淡一档，
//    否则内外同色、嵌套关系读不出来（描边同值，层级靠圆角 + 填充深浅区分）。
// L3_ALT 与 L3 配对，供**可无限递归**的嵌套详情区逐层互换（MCP 工具参数下钻）：
//    单一档位在第 3、4 层时相邻两框同色、只剩缩进可辨，交替则任意深度都有边界。
//    取 canvas 而非 panel——panel 与 surface 的明暗关系在亮暗主题里是相反的，
//    混用会让同一条嵌套链的深浅走向随主题翻转。
// POPOVER 浮层菜单（斜杠命令补全、字体下拉）：刻意不透明——菜单与弹窗不玻璃化。
export const CARD_L1 = 'rounded-2xl border border-line/45 bg-panel/70'
export const CARD_L2 = 'rounded-xl border border-line/60 bg-surface/50'
export const CARD_L3 = 'rounded-lg border border-line/60 bg-surface/30'
export const CARD_L3_ALT = 'rounded-lg border border-line/60 bg-canvas/45'

/** 递归嵌套区第 depth 层的卡壳（depth 从 1 起）：奇数层 ALT、偶数层 L3，逐层互换。 */
export const nestedTier = (depth: number): string =>
  depth % 2 === 1 ? CARD_L3_ALT : CARD_L3
export const POPOVER =
  'rounded-xl border border-line/40 bg-surface shadow-lg overflow-hidden'

// L1 可点卡片的悬停：底色收满 + 描边加重，与 L1 成对使用。
export const CARD_L1_HOVER = 'hover:bg-panel hover:border-line/70 transition'
