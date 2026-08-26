import { readJson } from './client'
import type { AlpacaFeed, AlpacaIntegrationStatus } from '../types/settings'

const endpoint = '/api/me/integrations/alpaca'

export async function getAlpacaIntegration(): Promise<AlpacaIntegrationStatus> {
  const response = await fetch(endpoint)
  return readJson(response, `GET ${endpoint}`) as Promise<AlpacaIntegrationStatus>
}

export async function saveAlpacaIntegration(input: {
  api_key: string
  secret_key: string
  feed: AlpacaFeed
}): Promise<AlpacaIntegrationStatus> {
  const response = await fetch(endpoint, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return readJson(response, `PUT ${endpoint}`) as Promise<AlpacaIntegrationStatus>
}

export async function verifyAlpacaIntegration(): Promise<AlpacaIntegrationStatus> {
  const verifyEndpoint = `${endpoint}/verify`
  const response = await fetch(verifyEndpoint, { method: 'POST' })
  return readJson(response, `POST ${verifyEndpoint}`) as Promise<AlpacaIntegrationStatus>
}

export async function removeAlpacaIntegration(): Promise<void> {
  const response = await fetch(endpoint, { method: 'DELETE' })
  if (!response.ok) await readJson(response, `DELETE ${endpoint}`)
}
