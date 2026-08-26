import type { ReactNode } from 'react'

import { useI18n } from '../i18n/I18nProvider'

export type ProductPage = 'historical' | 'factors' | 'portfolio' | 'walk-forward' | 'relationships' | 'discovery' | 'snapshots' | 'strategy' | 'data' | 'runs' | 'replay' | 'diagnose' | 'autopsy' | 'forward' | 'paper' | 'profile'

interface ProductNavProps {
  activePage: ProductPage
  onHistorical?: () => void
  onFactors?: () => void
  onPortfolio?: () => void
  onWalkForward?: () => void
  onRelationships?: () => void
  onDiscovery?: () => void
  onSnapshots?: () => void
  onStrategy: () => void
  onData: () => void
  onRuns: () => void
  onReplay: () => void
  onDiagnose: () => void
  onAutopsy: () => void
  onForward: () => void
  onPaper: () => void
  onProfile: () => void
}

function NavIcon({ page }: { page: ProductPage }) {
  const paths: Record<ProductPage, ReactNode> = {
    historical: <><path d="M3.5 15.5h13M5 13l3-3 2 1.5 4.5-6" /><path d="M4 4v12m0-12h12" /></>,
    factors: <><path d="M4 5h12M4 10h12M4 15h12" /><circle cx="7" cy="5" r="1.6" /><circle cx="13" cy="10" r="1.6" /><circle cx="9" cy="15" r="1.6" /></>,
    portfolio: <><path d="M4 15V9m4 6V5m4 10v-3m4 3V7" /><path d="M3 17h14" /></>,
    'walk-forward': <><path d="M3 14V8h4v6h3V5h4v9h3" /><path d="m12 3 2 2-2 2M3 17h14" /></>,
    relationships: <><circle cx="5" cy="6" r="2" /><circle cx="15" cy="6" r="2" /><circle cx="10" cy="15" r="2" /><path d="m6.7 7.2 2.2 5.9m4.4-5.9-2.2 5.9M7 6h6" /></>,
    discovery: <><path d="M4 5h12v10H4z" /><path d="M7 8h6M7 11h4" /><circle cx="14.5" cy="14.5" r="2" /></>,
    snapshots: <><path d="M5 4h10v12H5z" /><path d="M7.5 7h5M7.5 10h5M7.5 13h3" /><path d="M3 6V3h9" /></>,
    strategy: <><path d="M4 5h12M7 5v10m6-10v10M4 15h12" /><circle cx="7" cy="9" r="1.5" /><circle cx="13" cy="12" r="1.5" /></>,
    data: <><ellipse cx="10" cy="5" rx="6" ry="2.5" /><path d="M4 5v5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5m-12 5v5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5v-5" /></>,
    runs: <><path d="M5 3.5h8l3 3V17H5z" /><path d="M13 3.5V7h3M8 10h5m-5 3h5" /></>,
    replay: <><path d="M4.5 8A6 6 0 1 1 5 14" /><path d="M4.5 4.5V8H8" /><path d="m9 7 4 3-4 3z" /></>,
    diagnose: <><circle cx="9" cy="9" r="5" /><path d="m13 13 3.5 3.5M9 6v3l2 1" /></>,
    autopsy: <><path d="M3.5 15.5h13M5 13V8m3 5V4m3 9V6m3 7V3" /></>,
    forward: <><path d="M3.5 10h12" /><path d="m11.5 6 4 4-4 4M4 5v10" /></>,
    paper: <><rect x="3.5" y="5" width="13" height="10" rx="2" /><path d="M6 5V3.5h8V5m-7 5h6m-3-3v6" /></>,
    profile: <><circle cx="10" cy="7" r="3" /><path d="M4.5 17c.5-3.1 2.3-4.8 5.5-4.8s5 1.7 5.5 4.8" /></>,
  }
  return <svg className="nav-icon" viewBox="0 0 20 20" aria-hidden="true">{paths[page]}</svg>
}

function ProductNav({ activePage, onHistorical = () => undefined, onFactors = () => undefined, onPortfolio = () => undefined, onWalkForward = () => undefined, onRelationships = () => undefined, onDiscovery = () => undefined, onSnapshots = () => undefined, onStrategy, onData, onRuns, onReplay, onDiagnose, onAutopsy, onForward, onPaper, onProfile }: ProductNavProps) {
  const { language, setLanguage, tr } = useI18n()
  const research: Array<[ProductPage, string, () => void]> = [
    ['snapshots', 'Research Snapshots', onSnapshots],
    ['strategy', 'Strategy', onStrategy],
    ['data', 'Data', onData],
    ['runs', 'Runs', onRuns],
    ['replay', 'Replay', onReplay],
    ['diagnose', 'Diagnose', onDiagnose],
    ['autopsy', 'P&L Autopsy', onAutopsy],
  ]
  const discover: Array<[ProductPage, string, () => void]> = [['historical', 'Historical Market', onHistorical], ['factors', 'Factor Lab', onFactors], ['portfolio', 'Portfolio Lab', onPortfolio], ['walk-forward', 'Walk-Forward', onWalkForward], ['relationships', 'Factor Relationships', onRelationships], ['discovery', 'Strategy Discovery', onDiscovery]]
  const validate: Array<[ProductPage, string, () => void]> = [['forward', 'Forward', onForward], ['paper', 'Paper Trading', onPaper]]
  const renderItems = (items: Array<[ProductPage, string, () => void]>) => items.map(([id, label, action]) => <button key={id} type="button" data-page={id} aria-current={activePage === id ? 'page' : undefined} onClick={action}><NavIcon page={id} />{tr(label)}</button>)
  return (
    <aside className="product-sidebar">
      <div className="sidebar-brand"><span className="brand-mark" aria-hidden="true">VQD</span><div><strong>{tr('Visual Quant Debugger')}</strong><small>{tr('Quant research workspace')}</small></div></div>
      <nav className="sidebar-nav" aria-label={tr('Primary navigation')}>
        <span className="nav-group-label">{tr('DISCOVER')}</span>
        {renderItems(discover)}
        <span className="nav-group-label">{tr('RESEARCH')}</span>
        {renderItems(research)}
        <span className="nav-group-label">{tr('VALIDATE')}</span>
        {renderItems(validate)}
      </nav>
      <div className="sidebar-footer">
        <button className="profile-nav" type="button" aria-current={activePage === 'profile' ? 'page' : undefined} onClick={onProfile}><span aria-hidden="true"><NavIcon page="profile" /></span><div><strong>{tr('My')}</strong><small>{tr('Connections and preferences')}</small></div></button>
        <span>{tr('Trace protocol')} <code>1.0</code></span>
        <div className="language-switcher" role="group" aria-label={tr('Language switcher')}>
          <button type="button" aria-pressed={language === 'zh'} aria-label="切换为中文" onClick={() => setLanguage('zh')}>中</button>
          <button type="button" aria-pressed={language === 'en'} aria-label={tr('Switch to English')} onClick={() => setLanguage('en')}>EN</button>
        </div>
      </div>
    </aside>
  )
}

export default ProductNav
