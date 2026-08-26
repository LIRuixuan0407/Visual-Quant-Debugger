import { useCallback, useEffect, useState } from 'react'

import {
  getAlpacaIntegration,
  removeAlpacaIntegration,
  saveAlpacaIntegration,
  verifyAlpacaIntegration,
} from '../../api/settings'
import { listPaperAccounts } from '../../api/paper'
import { useI18n } from '../../i18n/I18nProvider'
import type { AlpacaFeed, AlpacaIntegrationStatus } from '../../types/settings'
import type { PaperAccount } from '../../types/paper'

function ProfilePage() {
  const { language, tr } = useI18n()
  const [status, setStatus] = useState<AlpacaIntegrationStatus | null>(null)
  const [accounts, setAccounts] = useState<PaperAccount[]>([])
  const [apiKey, setApiKey] = useState('')
  const [secretKey, setSecretKey] = useState('')
  const [feed, setFeed] = useState<AlpacaFeed>('iex')
  const [showSecret, setShowSecret] = useState(false)
  const [busy, setBusy] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [current, paperAccounts] = await Promise.all([getAlpacaIntegration(), listPaperAccounts()])
      setStatus(current)
      setAccounts(paperAccounts)
      setFeed(current.feed)
    }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : tr('Could not load connection settings.'))
    }
  }, [tr])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  async function saveAndVerify() {
    setBusy(true); setError(null); setNotice(null); setConfirmRemove(false)
    try {
      await saveAlpacaIntegration({ api_key: apiKey, secret_key: secretKey, feed })
      setApiKey(''); setSecretKey('')
      const verified = await verifyAlpacaIntegration()
      setStatus(verified)
      setNotice(tr('Alpaca connection verified.'))
    }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : tr('Could not save Alpaca connection.'))
      try { setStatus(await getAlpacaIntegration()) }
      catch { /* Preserve the actionable error from the save or verification request. */ }
    }
    finally { setBusy(false) }
  }

  async function verify() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const verified = await verifyAlpacaIntegration()
      setStatus(verified)
      setNotice(tr('Alpaca connection verified.'))
    }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : tr('Connection verification failed.'))
      try { setStatus(await getAlpacaIntegration()) }
      catch { /* Preserve the verification error. */ }
    }
    finally { setBusy(false) }
  }

  async function remove() {
    setBusy(true); setError(null); setNotice(null)
    try {
      await removeAlpacaIntegration()
      setConfirmRemove(false); setApiKey(''); setSecretKey(''); setFeed('iex')
      setStatus(await getAlpacaIntegration())
      setNotice(tr('Alpaca connection removed.'))
    }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : tr('Could not remove Alpaca connection.'))
    }
    finally { setBusy(false) }
  }

  const verified = status?.verification_status === 'VERIFIED'
  const stateLabel = !status?.configured ? 'NOT CONNECTED' : verified ? 'VERIFIED' : status.verification_status
  const account = accounts.find((item) => item.active_session_id) ?? accounts[0] ?? null
  const allocated = account ? account.equity - account.cash : 0
  const cashShare = account && account.equity > 0 ? Math.max(0, Math.min(100, account.cash / account.equity * 100)) : 100
  const positions = account ? Object.entries(account.positions).filter(([, quantity]) => quantity !== 0) : []
  const money = new Intl.NumberFormat(language === 'zh' ? 'zh-CN' : 'en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })

  return <main className="runs-shell profile-shell">
    <header className="workspace-title">
      <div><h1>{tr('My')}</h1><span>{tr('Connections and preferences for this VQD workspace')}</span></div>
      <span className={`status-badge ${verified ? 'connected' : status?.verification_status === 'FAILED' ? 'failed' : ''}`}>{tr(stateLabel)}</span>
    </header>

    <section className="portfolio-overview">
      <article className="portfolio-primary">
        <div className="portfolio-heading"><div><span>{tr('Your portfolio')}</span><strong>{account?.name ?? tr('No paper account')}</strong></div><span className="virtual-badge">{tr('SIMULATED')}</span></div>
        <div className="portfolio-value">{account ? money.format(account.equity) : '—'}</div>
        <span className="portfolio-caption">{tr('Total virtual equity')}</span>
        <div className="allocation-bar" aria-label={tr('Cash and allocated capital')}><i style={{ width: `${cashShare}%` }} /><b /></div>
        <div className="allocation-legend"><span><i className="cash-dot" />{tr('Cash')} <strong>{account ? money.format(account.cash) : '—'}</strong></span><span><i className="capital-dot" />{tr('Allocated')} <strong>{account ? money.format(allocated) : '—'}</strong></span></div>
      </article>
      <article className="balances-panel">
        <div className="portfolio-heading"><div><span>{tr('ACCOUNT')}</span><strong>{tr('Balances')}</strong></div><small>{account ? tr('Updated locally') : tr('Paper account required')}</small></div>
        <dl className="balance-list">
          <div><dt>{tr('Cash')}</dt><dd>{account ? money.format(account.cash) : '—'}</dd></div>
          <div><dt>{tr('Positions value')}</dt><dd>{account ? money.format(allocated) : '—'}</dd></div>
          <div><dt>{tr('Fees paid')}</dt><dd>{account ? money.format(account.cumulative_fees) : '—'}</dd></div>
        </dl>
        <div className="portfolio-positions"><span>{tr('Open positions')}</span>{positions.length > 0 ? positions.slice(0, 4).map(([symbol, quantity]) => <div key={symbol}><strong>{symbol}</strong><code>{quantity}</code></div>) : <small>{tr('No open positions.')}</small>}</div>
      </article>
    </section>

    <section className="profile-evidence" aria-label={tr('Connection summary')}>
      <div><span>{tr('Market data')}</span><strong>Alpaca</strong></div>
      <div><span>{tr('API Key')}</span><code>{status?.masked_api_key ?? tr('Not configured')}</code></div>
      <div><span>{tr('Feed')}</span><strong>{(status?.feed ?? feed).toUpperCase()}</strong></div>
      <div><span>{tr('Credential source')}</span><strong>{tr(status?.source ?? 'NONE')}</strong></div>
    </section>

    <section className="profile-grid">
      <article className="workspace-panel integration-form-panel">
        <div className="section-heading"><div><span className="section-kicker">ALPACA</span><h2>{tr('Market data connection')}</h2></div><span>{tr('Read-only market data')}</span></div>
        <p className="profile-intro">{tr('Enter your own Alpaca credentials. VQD uses them only for stock information, historical bars, and realtime market data.')}</p>
        <form onSubmit={(event) => { event.preventDefault(); void saveAndVerify() }} className="integration-form">
          <label>
            <span>{tr('API Key')}</span>
            <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="username" placeholder={status?.configured ? tr('Enter a new key to replace the current one') : 'PK...'} required minLength={8} />
          </label>
          <label>
            <span>{tr('Secret Key')}</span>
            <div className="secret-control"><input type={showSecret ? 'text' : 'password'} value={secretKey} onChange={(event) => setSecretKey(event.target.value)} autoComplete="new-password" placeholder={status?.configured ? tr('Enter a new secret to replace the current one') : tr('Your Alpaca secret')} required minLength={8} /><button type="button" onClick={() => setShowSecret((current) => !current)}>{tr(showSecret ? 'Hide' : 'Show')}</button></div>
          </label>
          <fieldset>
            <legend>{tr('Default market feed')}</legend>
            <label className={feed === 'iex' ? 'selected' : ''}><input type="radio" name="feed" value="iex" checked={feed === 'iex'} onChange={() => setFeed('iex')} /><span><strong>IEX</strong><small>{tr('Single-exchange feed')}</small></span></label>
            <label className={feed === 'sip' ? 'selected' : ''}><input type="radio" name="feed" value="sip" checked={feed === 'sip'} onChange={() => setFeed('sip')} /><span><strong>SIP</strong><small>{tr('Consolidated US feed · subscription required')}</small></span></label>
          </fieldset>
          {error && <p className="inline-error" role="alert">{error}</p>}
          {notice && <p className="profile-notice" role="status">{notice}</p>}
          <div className="profile-actions">
            <button className="primary-action" type="submit" disabled={busy || apiKey.length < 8 || secretKey.length < 8}>{tr(busy ? 'Working…' : 'Save and verify')}</button>
            {status?.configured && <button type="button" disabled={busy} onClick={() => void verify()}>{tr('Verify again')}</button>}
          </div>
        </form>
      </article>

      <aside className="workspace-panel integration-status-panel">
        <div className="section-heading"><h2>{tr('Connection status')}</h2><span className={`status-badge ${verified ? 'connected' : status?.verification_status === 'FAILED' ? 'failed' : ''}`}>{tr(stateLabel)}</span></div>
        <dl className="connection-facts">
          <div><dt>{tr('Stored as')}</dt><dd>{tr(status?.source ?? 'NONE')}</dd></div>
          <div><dt>{tr('Default feed')}</dt><dd>{(status?.feed ?? feed).toUpperCase()}</dd></div>
          <div><dt>{tr('Last verified')}</dt><dd>{status?.last_verified_at ? new Date(status.last_verified_at).toLocaleString() : tr('Never')}</dd></div>
        </dl>
        {status?.last_error && <p className="connection-error">{tr(status.last_error)}</p>}
        <div className="security-note"><strong>{tr('Market data only')}</strong><p>{tr('VQD never sends real broker orders. Credentials are encrypted on the backend and are never returned to the browser.')}</p></div>
        {status?.source === 'ENVIRONMENT' && <p className="profile-help">{tr('This connection is managed by environment variables. Saving above will replace it with your encrypted workspace configuration.')}</p>}
        {status?.removable && <div className="remove-connection">
          {!confirmRemove ? <button type="button" disabled={busy} onClick={() => setConfirmRemove(true)}>{tr('Remove connection')}</button> : <><span>{tr('Remove the saved credentials?')}</span><button type="button" className="danger-action" disabled={busy} onClick={() => void remove()}>{tr('Confirm remove')}</button><button type="button" onClick={() => setConfirmRemove(false)}>{tr('Cancel')}</button></>}
        </div>}
      </aside>
    </section>
  </main>
}

export default ProfilePage
