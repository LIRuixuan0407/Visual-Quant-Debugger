interface ReplayControlsProps {
  previousBarId: string | null
  nextBarId: string | null
  previousSignalId: string | null
  nextSignalId: string | null
  onSelect: (eventId: string) => void
}

function ReplayControls({ previousBarId, nextBarId, previousSignalId, nextSignalId, onSelect }: ReplayControlsProps) {
  const { tr } = useI18n()
  return (
    <nav className="replay-controls" aria-label={tr('Replay navigation')}>
      <button type="button" disabled={!previousBarId} onClick={() => previousBarId && onSelect(previousBarId)}>
        <span aria-hidden="true">←</span> {tr('Previous bar')}
      </button>
      <button type="button" disabled={!previousSignalId} onClick={() => previousSignalId && onSelect(previousSignalId)}>
        {tr('Previous signal')}
      </button>
      <span className="control-divider" aria-hidden="true" />
      <button type="button" disabled={!nextSignalId} onClick={() => nextSignalId && onSelect(nextSignalId)}>
        {tr('Next signal')}
      </button>
      <button type="button" disabled={!nextBarId} onClick={() => nextBarId && onSelect(nextBarId)}>
        {tr('Next bar')} <span aria-hidden="true">→</span>
      </button>
    </nav>
  )
}

export default ReplayControls
import { useI18n } from '../../i18n/I18nProvider'
