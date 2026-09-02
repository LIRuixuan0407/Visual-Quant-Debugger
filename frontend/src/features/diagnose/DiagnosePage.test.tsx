import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import { diagnosisReport, whatIfScenario } from '../../test/fixtures/diagnosis'
import DiagnosePage from './DiagnosePage'

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

test('keeps parameter, stress, statistical, regime, fingerprint, and What-if diagnostics', async () => {
  const onOpenReplay = vi.fn()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(diagnosisReport))
  render(<DiagnosePage traceId="trace-custom" onOpenReplay={onOpenReplay} />)

  expect(await screen.findByRole('heading', { name: 'Diagnose' })).toBeInTheDocument()
  const trainTestHeading = screen.getByRole('heading', { name: 'Train / Test · 70 / 30' })
  expect(trainTestHeading).toBeInTheDocument()
  expect(trainTestHeading.closest('section')?.querySelector('.train-test-table')).toBeInTheDocument()
  const fingerprintHeading = screen.getByRole('heading', { name: 'Strategy failure fingerprint' })
  expect(screen.getByText('2 high-severity and 2 medium-severity failure modes across 6 evidence-backed dimensions.')).toBeInTheDocument()
  expect(screen.getByText('Out-of-sample degradation')).toBeInTheDocument()
  const parameterHeading = screen.getByRole('heading', { name: 'Lookback sensitivity' })
  expect(screen.getByRole('img', { name: 'Train and test Sharpe across lookback candidates' })).toBeInTheDocument()
  const costStressSection = screen.getByRole('heading', { name: 'Cost stress' }).closest('section') as HTMLElement
  const delaySection = screen.getByRole('heading', { name: 'Execution delay' }).closest('section') as HTMLElement
  expect(within(costStressSection).getByText('20 bps')).toBeInTheDocument()
  expect(within(delaySection).getByText('close(t+3)')).toBeInTheDocument()
  const statisticalHeading = screen.getByRole('heading', { name: 'Statistical diagnostics' })
  expect(statisticalHeading).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Return and squared return autocorrelation by lag' })).toBeInTheDocument()
  expect(screen.getByText('0.180')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Pair mean-reversion evidence' })).toBeInTheDocument()
  expect(screen.getByText('Time-adjacent AR(1) pairs').closest('div')).toHaveTextContent('35')
  expect(screen.getByText('This is diagnostic evidence, not proof of stationarity or cointegration.')).toBeInTheDocument()
  const volatilityHeading = screen.getByRole('heading', { name: 'Volatility diagnostics' })
  const volatilityChart = screen.getByRole('img', { name: 'Historical and EWMA volatility with strategy drawdown overlays' })
  expect(volatilityHeading).toBeInTheDocument()
  expect(volatilityChart).toBeInTheDocument()
  expect(within(volatilityHeading.closest('section') as HTMLElement).getByText('HIGH')).toBeInTheDocument()
  expect(screen.getByText('1 of the 1 evaluable largest drawdowns began while EWMA volatility was rising.')).toBeInTheDocument()
  const regimeHeading = screen.getByRole('heading', { name: 'Market regime diagnostics' })
  expect(screen.getByRole('table', { name: 'Strategy performance by market regime' })).toHaveTextContent('HIGH / DOWNTREND')
  expect(screen.getByText('REGIME_DEPENDENT')).toBeInTheDocument()
  const whatIfHeading = screen.getByRole('heading', { name: 'What-if Lab' })
  expect(whatIfHeading).toBeInTheDocument()
  expect(fingerprintHeading.compareDocumentPosition(parameterHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(parameterHeading.compareDocumentPosition(statisticalHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(statisticalHeading.compareDocumentPosition(volatilityHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(volatilityHeading.compareDocumentPosition(regimeHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(regimeHeading.compareDocumentPosition(whatIfHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.getByText('BASELINE_READY')).toBeInTheDocument()
  expect(screen.getByText('Total return').closest('.what-if-row')).toHaveTextContent('1.20%1.20%0.00 pp')
  expect(screen.getByText('Execution timing changes are measured, not inferred')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/diagnostics', expect.objectContaining({ method: 'POST' }))

  fireEvent.click(screen.getByRole('button', { name: 'Open in Replay →' }))
  expect(onOpenReplay).toHaveBeenCalledTimes(1)
})

test('runs a combined What-if scenario and keeps baseline beside stressed metrics', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(jsonResponse(diagnosisReport))
    .mockResolvedValueOnce(jsonResponse(whatIfScenario))
  render(<DiagnosePage traceId="trace-custom" onOpenReplay={() => undefined} />)

  expect(await screen.findByRole('heading', { name: 'What-if Lab' })).toBeInTheDocument()
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Transaction cost' }), { target: { value: '20' } })
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Slippage' }), { target: { value: '12' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Execution delay' }), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Run scenario' }))

  expect(await screen.findByText('LOWER_NET_PNL')).toBeInTheDocument()
  const whatIfSection = screen.getByRole('heading', { name: 'What-if Lab' }).closest('section') as HTMLElement
  expect(within(whatIfSection).getByText('Sharpe').closest('.what-if-row')).toHaveTextContent('1.400.71-0.69')
  expect(within(whatIfSection).getByText('Net P&L').closest('.what-if-row')).toHaveTextContent('$1,200.00$600.00-$600.00')
  expect(within(whatIfSection).getByText('Turnover').closest('.what-if-row')?.querySelector('code:last-child')).toHaveClass('neutral')
  expect(within(whatIfSection).getByText('Trade count').closest('.what-if-row')?.querySelector('code:last-child')).toHaveClass('neutral')
  expect(screen.getByText('Sharpe changes from 1.400 to 0.710.')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Cost stress' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Execution delay' })).toBeInTheDocument()
  expect(fetchMock).toHaveBeenLastCalledWith('/api/diagnostics/what-if', expect.objectContaining({
    method: 'POST', body: expect.stringContaining('"fee_bps":20'),
  }))
})

test('shows explicit no-trace and retryable API error states', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ detail: 'Run context missing' }, 404))
  const { rerender } = render(<DiagnosePage traceId={null} onOpenReplay={() => undefined} />)
  expect(screen.getByText('Run a backtest from Strategy before diagnosing it.')).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()

  rerender(<DiagnosePage traceId="trace-missing" onOpenReplay={() => undefined} />)
  expect(await screen.findByRole('heading', { name: 'Could not diagnose this trace.' })).toBeInTheDocument()
  expect(screen.getByText('POST /api/diagnostics returned 404. Run context missing')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
})

test('labels unsupported framework reruns instead of rendering empty diagnostics as results', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
    ...diagnosisReport,
    lookback_sensitivity: [], cost_stress: [], execution_delay: [],
    support: { train_test: 'AVAILABLE', parameter_sensitivity: 'NOT_SUPPORTED', cost_stress: 'NOT_SUPPORTED', execution_delay: 'NOT_SUPPORTED' },
    what_if: { status: 'NOT_SUPPORTED', baseline_inputs: null, baseline_metrics: null, parameter: null, calculation_details: [] },
  }))
  render(<DiagnosePage traceId="trace-framework" onOpenReplay={() => undefined} />)
  expect(await screen.findAllByText('Not supported for this run')).toHaveLength(4)
  expect(screen.getByText('What-if requires a native VQD rerun contract.')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /Historical and EWMA volatility/ })).toBeInTheDocument()
})

test('renders unreliable dataset frequency as unsupported without an annualized chart', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
    ...diagnosisReport,
    volatility_diagnostics: {
      ...diagnosisReport.volatility_diagnostics!,
      status: 'UNSUPPORTED', dataset_frequency: '1Hour', annualization_factor: null,
      points: diagnosisReport.volatility_diagnostics!.points.map((point) => ({
        ...point, rolling_historical_vol: null, ewma_vol: null, regime: null,
      })),
      current_regime: null, current_historical_vol: null, current_ewma_vol: null,
      drawdown_overlap: [], evaluable_drawdown_count: 0,
      rising_volatility_start_count: 0, regime_change_start_count: 0,
      verdict: 'UNSUPPORTED',
      summary: "Annualized volatility is unsupported for dataset frequency '1Hour'.",
    },
    regime_diagnostics: {
      status: 'UNSUPPORTED', trend_window: 21, trend_threshold: 0.02, performance: [], verdict: 'UNSUPPORTED',
      summary: 'Regime diagnostics require supported volatility diagnostics.', calculation_details: [],
    },
  }))
  render(<DiagnosePage traceId="trace-hourly" onOpenReplay={() => undefined} />)

  expect(await screen.findByText("Annualized volatility is unsupported for dataset frequency '1Hour'.")).toBeInTheDocument()
  expect(screen.queryByRole('img', { name: /Historical and EWMA volatility/ })).not.toBeInTheDocument()
  expect(screen.getByText('1Hour')).toBeInTheDocument()
  expect(screen.getAllByText('Unavailable')).toHaveLength(2)
})

test('hides pair evidence for non-pairs strategies while retaining return diagnostics', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
    ...diagnosisReport,
    source_run: { ...diagnosisReport.source_run, strategy_id: 'user.sma-cross' },
  }))
  render(<DiagnosePage traceId="trace-single" onOpenReplay={() => undefined} />)

  expect(await screen.findByRole('heading', { name: 'Statistical diagnostics' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Return and squared return autocorrelation by lag' })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Pair mean-reversion evidence' })).not.toBeInTheDocument()
})

test('keeps legacy cached reports readable when new diagnostics are absent', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
    ...diagnosisReport,
    statistical_diagnostics: undefined,
    volatility_diagnostics: undefined,
    regime_diagnostics: undefined,
    failure_fingerprint: undefined,
    what_if: undefined,
  }))
  render(<DiagnosePage traceId="trace-legacy" onOpenReplay={() => undefined} />)

  expect(await screen.findByRole('heading', { name: 'Statistical diagnostics' })).toBeInTheDocument()
  expect(screen.getAllByText('Not available in this cached report')).toHaveLength(4)
  expect(screen.getByRole('heading', { name: 'What-if Lab' })).toBeInTheDocument()
})

test('renders statistical diagnostics and evidence boundary in Chinese', async () => {
  window.localStorage.setItem('vqd-language', 'zh')
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(diagnosisReport))
  render(<I18nProvider><DiagnosePage traceId="trace-custom" onOpenReplay={() => undefined} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: '统计诊断' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: '按滞后阶数比较收益率与平方收益率自相关' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '配对均值回归证据' })).toBeInTheDocument()
  expect(screen.getByText('这些结果是诊断证据，不是平稳性或协整关系的证明。')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '策略失效指纹' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '波动率诊断' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: '历史波动率、EWMA 波动率与策略回撤叠加图' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '市场状态诊断' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'What-if 实验室' })).toBeInTheDocument()
  expect(screen.getByText('运行场景')).toBeInTheDocument()
})
