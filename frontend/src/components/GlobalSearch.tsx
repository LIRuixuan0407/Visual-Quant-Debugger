import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import { globalSearch } from '../api/search'
import { useI18n } from '../i18n/I18nProvider'
import { SEARCH_ENTITY_TYPES, type RecentSearchItem, type SearchEntityType, type SearchOpenTarget, type SearchResult } from '../types/search'
import { readRecentSearches, recordRecentSearch } from '../utils/recentSearches'

interface GlobalSearchProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onNavigate: (item: SearchOpenTarget) => void
}

function typeLabel(entityType: SearchEntityType): string {
  return entityType.replaceAll('_', ' ')
}

function ResultRow({ item, active, onSelect, onHover }: { item: SearchResult; active: boolean; onSelect: () => void; onHover: () => void }) {
  const { tr } = useI18n()
  return <button type="button" role="option" aria-selected={active} className="global-search-result" onMouseEnter={onHover} onClick={onSelect}>
    <span className="search-result-type">{tr(typeLabel(item.entity_type))}</span>
    <span className="search-result-copy"><strong>{tr(item.title)}</strong><small>{tr(item.subtitle)}</small><code>{item.entity_id}</code></span>
    <span className="search-result-match">{item.highlights.map((field) => tr(field)).join(' · ')}</span>
  </button>
}

function RecentRow({ item, active, onSelect, onHover }: { item: RecentSearchItem; active: boolean; onSelect: () => void; onHover: () => void }) {
  const { tr } = useI18n()
  return <button type="button" role="option" aria-selected={active} className="global-search-result recent" onMouseEnter={onHover} onClick={onSelect}>
    <span className="search-result-type">{tr(typeLabel(item.entity_type))}</span>
    <span className="search-result-copy"><strong>{tr(item.title)}</strong><code>{item.entity_id}</code></span>
    <time dateTime={item.last_opened_at}>{tr('Recent')}</time>
  </button>
}

export default function GlobalSearch({ open, onOpenChange, onNavigate }: GlobalSearchProps) {
  const { tr } = useI18n()
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [selectedType, setSelectedType] = useState<SearchEntityType | 'ALL'>('ALL')
  const [results, setResults] = useState<SearchResult[]>([])
  const [recent, setRecent] = useState<RecentSearchItem[]>(readRecentSearches)
  const [activeIndex, setActiveIndex] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const hasQuery = query.trim().length > 0
  const visibleRecent = useMemo(() => selectedType === 'ALL' ? recent : recent.filter((item) => item.entity_type === selectedType), [recent, selectedType])
  const itemCount = hasQuery ? results.length : visibleRecent.length

  useEffect(() => {
    function shortcut(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        onOpenChange(true)
      } else if (event.key === 'Escape' && open) {
        event.preventDefault()
        onOpenChange(false)
      }
    }
    document.addEventListener('keydown', shortcut)
    return () => document.removeEventListener('keydown', shortcut)
  }, [onOpenChange, open])

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(timer)
  }, [open])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      if (!query.trim()) {
        setResults([]); setLoading(false); setError(null); setActiveIndex(0)
        return
      }
      setLoading(true); setError(null)
      void globalSearch(query, selectedType === 'ALL' ? [] : [selectedType])
        .then((response) => {
          if (!cancelled) { setResults(response.results); setActiveIndex(0) }
        })
        .catch((reason: unknown) => {
          if (!cancelled) { setResults([]); setError(reason instanceof Error ? reason.message : String(reason)) }
        })
        .finally(() => { if (!cancelled) setLoading(false) })
    }, 120)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [open, query, selectedType])

  function close() {
    onOpenChange(false)
    setQuery(''); setSelectedType('ALL'); setResults([]); setError(null); setActiveIndex(0)
  }

  function openItem(item: SearchOpenTarget) {
    setRecent(recordRecentSearch(item))
    onNavigate(item)
    close()
  }

  function handleKeys(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((current) => itemCount === 0 ? 0 : (current + 1) % itemCount)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((current) => itemCount === 0 ? 0 : (current - 1 + itemCount) % itemCount)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const selected = hasQuery ? results[activeIndex] : visibleRecent[activeIndex]
      if (selected) openItem(selected)
    } else if (event.key === 'Escape') {
      event.preventDefault(); close()
    }
  }

  if (!open) return null
  return <div className="global-search-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <section className="global-search-dialog" role="dialog" aria-modal="true" aria-label={tr('Global Search')}>
      <header className="global-search-input-row">
        <span aria-hidden="true" className="search-glyph">⌕</span>
        <input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={handleKeys} aria-label={tr('Search research workspace')} placeholder={tr('Search IDs, names, symbols, and tags…')} autoComplete="off" />
        <select aria-label={tr('Search type')} value={selectedType} onChange={(event) => { setSelectedType(event.target.value as SearchEntityType | 'ALL'); setActiveIndex(0) }}>
          <option value="ALL">{tr('All types')}</option>
          {SEARCH_ENTITY_TYPES.map((entityType) => <option value={entityType} key={entityType}>{tr(typeLabel(entityType))}</option>)}
        </select>
        <kbd>ESC</kbd>
      </header>
      <div className="global-search-status"><strong>{tr(hasQuery ? 'Results' : 'Recent')}</strong><span>{tr('↑↓ Navigate · Enter Open · Esc Close')}</span></div>
      <div className="global-search-results" role="listbox" aria-label={tr(hasQuery ? 'Search results' : 'Recent searches')}>
        {loading && <p className="search-message">{tr('Searching…')}</p>}
        {error && <p className="search-message error" role="alert">{tr('Search failed')}: {tr(error)}</p>}
        {!loading && !error && hasQuery && results.length === 0 && <p className="search-message">{tr('No matching research objects.')}</p>}
        {!loading && !error && !hasQuery && visibleRecent.length === 0 && <p className="search-message">{tr('No recent research objects.')}</p>}
        {!loading && !error && hasQuery && results.map((item, index) => <ResultRow key={`${item.entity_type}:${item.entity_id}`} item={item} active={index === activeIndex} onHover={() => setActiveIndex(index)} onSelect={() => openItem(item)} />)}
        {!loading && !error && !hasQuery && visibleRecent.map((item, index) => <RecentRow key={`${item.entity_type}:${item.entity_id}`} item={item} active={index === activeIndex} onHover={() => setActiveIndex(index)} onSelect={() => openItem(item)} />)}
      </div>
      <footer><span>{tr('Deterministic local search')}</span><span>{tr('No Trace events or source files are searched.')}</span></footer>
    </section>
  </div>
}
