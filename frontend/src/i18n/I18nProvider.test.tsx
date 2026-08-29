import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, test } from 'vitest'

import ProductNav from '../components/ProductNav'
import { I18nProvider, useI18n } from './I18nProvider'

const noop = () => undefined

function renderNavigation() {
  return render(
    <I18nProvider>
      <ProductNav
        activePage="strategy"
        onStrategy={noop}
        onData={noop}
        onRuns={noop}
        onReplay={noop}
        onDiagnose={noop}
        onAutopsy={noop}
        onForward={noop}
        onPaper={noop}
        onProfile={noop}
      />
    </I18nProvider>,
  )
}

function AdapterTranslations() {
  const { tr } = useI18n()
  return <div>{tr('Runtime')} · {tr('BASIC')} · {tr('Point-in-time provenance not available')} · {tr('Not supported for this run')}</div>
}

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.lang = 'zh-CN'
})

test('defaults to Chinese and persists an explicit English selection', async () => {
  const view = renderNavigation()

  expect(screen.getByRole('button', { name: '策略' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('button', { name: '数据' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '研究记录' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '前向验证' })).toBeInTheDocument()
  expect(screen.getByText('研究')).toBeInTheDocument()
  expect(screen.getByText('可视化量化调试器')).toBeInTheDocument()
  await waitFor(() => expect(document.documentElement.lang).toBe('zh-CN'))

  fireEvent.click(screen.getByRole('button', { name: '切换为英文' }))
  expect(screen.getByRole('button', { name: 'Strategy' })).toHaveAttribute('aria-current', 'page')
  expect(window.localStorage.getItem('vqd-language')).toBe('en')
  await waitFor(() => expect(document.documentElement.lang).toBe('en'))

  view.unmount()
  renderNavigation()
  expect(screen.getByRole('button', { name: 'Strategy' })).toBeInTheDocument()
})

test('covers user-visible adapter capability labels in Chinese mode', () => {
  render(<I18nProvider><AdapterTranslations /></I18nProvider>)
  expect(screen.getByText('运行时 · 基础 · 时点数据来源不可用 · 本次运行不支持')).toBeInTheDocument()
})

function IntegrityTranslations() {
  const { tr } = useI18n()
  const items = [
    tr('The Hypothesis changed without a matching research ledger event, so the change was applied outside the disciplined revision flow.'),
    tr('dataset fingerprint drifted from sha256:a to sha256:b'),
    tr("run 'run-1' executed with fee_bps 25.0 while the research portfolio defines 5.0"),
    tr("factor research 'fr-1' RESEARCH evaluation timeline reaches outside its RESEARCH window at 2026-01-01T00:00:00Z"),
    tr('VALIDATE event at 2026-01-01T00:00:00Z modified this hypothesis after Holdout reveal'),
    tr('portfolio top percent is 40.0 while the hypothesis candidate defines 20.0'),
    tr("run 'run-2' does not own Trace 'trace-2'"),
  ]
  return <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
}

test('translates research integrity reasons and evidence in Chinese mode', () => {
  render(<I18nProvider><IntegrityTranslations /></I18nProvider>)
  expect(screen.getByText('该研究假设在没有对应研究台账事件的情况下发生了变化，说明修改绕过了受控的修订流程。')).toBeInTheDocument()
  expect(screen.getByText('数据集指纹从 sha256:a 漂移到 sha256:b')).toBeInTheDocument()
  expect(screen.getByText('运行“run-1”以 fee_bps 25.0 执行，而研究组合定义为 5.0')).toBeInTheDocument()
  expect(screen.getByText('因子研究“fr-1”的研究评估时间线在 2026-01-01T00:00:00Z 越出了研究窗口')).toBeInTheDocument()
  expect(screen.getByText('VALIDATE 事件于 2026-01-01T00:00:00Z 在 Holdout 揭示后修改了该研究假设')).toBeInTheDocument()
  expect(screen.getByText('组合的头部百分比为 40.0，而研究假设候选定义为 20.0')).toBeInTheDocument()
  expect(screen.getByText('运行“run-2”不拥有追踪“trace-2”')).toBeInTheDocument()
})

function WorkspaceTranslations() {
  const { tr } = useI18n()
  return <div>
    <span>{tr('Research Workspace')}</span>
    <span>{tr('3 Factor research revisions are linked.')}</span>
    <span>{tr('2 immutable Run / Trace pairs are linked.')}</span>
    <span>{tr('Holdout is sealed. Confirming will reveal it for this immutable Hypothesis revision; no parameter or Idea is changed automatically.')}</span>
  </div>
}

function ProductText() {
  const { tr } = useI18n()
  return <span>{tr('Portfolio · internal milestone real quality research')}</span>
}

test('removes internal milestone numbering from product-visible labels', () => {
  render(<I18nProvider><ProductText /></I18nProvider>)
  expect(screen.getByText('Portfolio · internal milestone real quality research')).toBeInTheDocument()
  expect(screen.queryByText(/internal build label/i)).not.toBeInTheDocument()
})

test('translates unified workspace stages and explicit Holdout boundary in Chinese mode', () => {
  render(<I18nProvider><WorkspaceTranslations /></I18nProvider>)
  expect(screen.getByText('研究工作台')).toBeInTheDocument()
  expect(screen.getByText('已关联 3 个因子研究修订版。')).toBeInTheDocument()
  expect(screen.getByText('已关联 2 对不可变的运行 / 追踪记录。')).toBeInTheDocument()
  expect(screen.getByText(/不会自动修改任何参数或研究构想/)).toBeInTheDocument()
})

function AttributionTranslations() {
  const { tr } = useI18n()
  return <div>
    <span>{tr('Backtest vs Paper Attribution')}</span>
    <span>{tr('Attribution Waterfall')}</span>
    <span>{tr('Unknown effects remain in Residual')}</span>
    <span>{tr('Paper execution timing differs from the same-decision Recorded Feed Reference')}</span>
    <span>{tr('First broker event occurred_at: 2026-08-29T01:30:00+00:00')}</span>
    <span>{tr('Backtest period: 2026-01-01 → 2026-01-31')}</span>
    <span>{tr('Recorded market events: 42')}</span>
    <span>{tr('Recorded fees difference from Reference to Paper')}</span>
  </div>
}

test('translates attribution labels and evidence boundaries in Chinese mode', () => {
  render(<I18nProvider><AttributionTranslations /></I18nProvider>)
  expect(screen.getByText('回测与虚拟盘差异归因')).toBeInTheDocument()
  expect(screen.getByText('归因瀑布图')).toBeInTheDocument()
  expect(screen.getByText('无法可靠拆分的影响保留在残差中')).toBeInTheDocument()
  expect(screen.getByText('在决策一致的情况下，虚拟盘成交时间与已记录行情参考运行不同')).toBeInTheDocument()
  expect(screen.getByText('首个券商事件时间：2026-08-29T01:30:00+00:00')).toBeInTheDocument()
  expect(screen.getByText('回测周期：2026-01-01 → 2026-01-31')).toBeInTheDocument()
  expect(screen.getByText('已记录行情事件：42')).toBeInTheDocument()
  expect(screen.getByText('已记录费用在参考运行与虚拟盘之间存在差异')).toBeInTheDocument()
})

function ResearchBundleTranslations() {
  const { tr } = useI18n()
  return <div>
    <span>{tr('Research Bundle')}</span>
    <span>{tr('Import Preview')}</span>
    <span>{tr('Bundle Contents')}</span>
    <span>{tr('Checksum inventory')}</span>
    <span>{tr('Immutable research record can be imported.')}</span>
    <span>{tr('Any Python source carried by a Bundle is treated as inert evidence. Bundle import never executes or automatically registers custom code.')}</span>
  </div>
}

test('translates Research Bundle portability and safety boundaries in Chinese mode', () => {
  render(<I18nProvider><ResearchBundleTranslations /></I18nProvider>)
  expect(screen.getByText('研究包')).toBeInTheDocument()
  expect(screen.getByText('导入预览')).toBeInTheDocument()
  expect(screen.getByText('研究包内容')).toBeInTheDocument()
  expect(screen.getByText('校验和清单')).toBeInTheDocument()
  expect(screen.getByText('可以导入这份不可变研究记录。')).toBeInTheDocument()
  expect(screen.getByText(/绝不会执行或自动注册自定义代码/)).toBeInTheDocument()
})
