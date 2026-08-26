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
