export type AlpacaFeed = 'iex' | 'sip'
export type CredentialSource = 'VAULT' | 'ENVIRONMENT' | 'NONE'
export type VerificationStatus = 'UNVERIFIED' | 'VERIFIED' | 'FAILED'

export interface AlpacaIntegrationStatus {
  provider: 'alpaca'
  configured: boolean
  source: CredentialSource
  masked_api_key: string | null
  feed: AlpacaFeed
  verification_status: VerificationStatus
  last_verified_at: string | null
  last_error: string | null
  removable: boolean
}
