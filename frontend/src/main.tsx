import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/jetbrains-mono'

import App from './App'
import { WorkspaceProvider } from './features/workspaces/WorkspaceContext'
import { I18nProvider } from './i18n/I18nProvider'
import './styles.css'
import './ui2.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider><WorkspaceProvider><App /></WorkspaceProvider></I18nProvider>
  </StrictMode>,
)
